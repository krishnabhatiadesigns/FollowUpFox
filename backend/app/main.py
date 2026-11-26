from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
from statistics import mean
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
import requests
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from itsdangerous import BadSignature, URLSafeTimedSerializer
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from groq import GroqError
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from .firebase_store import FirebaseRealtimeStore, FirebaseStoreError, create_firebase_session
from .resume_matching import extract_resume_text, fallback_skills, parse_skill_response


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_RESUME_BYTES = 10 * 1024 * 1024


class Settings(BaseSettings):
    firebase_database_url: str = ""
    firebase_service_account_path: str = ""
    firebase_service_account_json: str = ""
    frontend_url: str = "http://localhost:5173"
    session_secret: str = "development-only-change-me"
    token_encryption_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    model_config = SettingsConfigDict(env_file=(".env", "backend/.env"), extra="ignore")


settings = Settings()
print("Firebase URL loaded:", repr(settings.firebase_database_url))
print("Firebase URL valid:", (
    settings.firebase_database_url.startswith("https://")
    and not settings.firebase_database_url.endswith(".json")
))
if not settings.token_encryption_key:
    settings.token_encryption_key = Fernet.generate_key().decode()
if settings.google_redirect_uri.startswith(("http://localhost", "http://127.0.0.1")):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
fernet = Fernet(settings.token_encryption_key.encode())
signer = URLSafeTimedSerializer(settings.session_secret, salt="outreachos-session")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320, collation="NOCASE"), unique=True, index=True)
    name: Mapped[str] = mapped_column("full_name", String(255))
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_type: Mapped[str] = mapped_column(String(30), default="fresher")
    plan: Mapped[str] = mapped_column(String(30), default="free")
    daily_draft_limit: Mapped[int] = mapped_column(Integer, default=10)
    daily_send_limit: Mapped[int] = mapped_column(Integer, default=20)
    gmail_connected: Mapped[bool] = mapped_column(default=False)
    professional_headline: Mapped[str] = mapped_column(String(500), default="")
    about: Mapped[str] = mapped_column(Text, default="")
    human_approval_required: Mapped[bool] = mapped_column(default=True)
    followup_delay_days: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    outreach: Mapped[list[Outreach]] = relationship(back_populates="user", cascade="all, delete-orphan")
    resume_analyses: Mapped[list[ResumeAnalysis]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserVisit(Base):
    __tablename__ = "user_visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    visit_date: Mapped[datetime] = mapped_column(DateTime, index=True)


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    job_title: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(320, collation="NOCASE"), unique=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20), default="my", index=True)
    source: Mapped[str] = mapped_column(String(255), default="user")
    bounce_count: Mapped[int] = mapped_column(Integer, default=0)
    opted_out: Mapped[bool] = mapped_column(default=False)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255, collation="NOCASE"), unique=True)
    industry: Mapped[str] = mapped_column(String(255))
    size_band: Mapped[str] = mapped_column(String(30), default="unknown")
    hq_city: Mapped[str] = mapped_column(String(255))


class ResumeAnalysis(Base):
    __tablename__ = "resumes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column("file_name", String(255))
    extracted_text: Mapped[str] = mapped_column("raw_text", Text)
    skills: Mapped[str] = mapped_column("skills_json", Text, default="[]")
    file_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user: Mapped[User] = relationship(back_populates="resume_analyses")


class ResumeSkill(Base):
    __tablename__ = "resume_skills"
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
    confidence: Mapped[float] = mapped_column(default=1.0)


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255, collation="NOCASE"), unique=True)
    category: Mapped[str] = mapped_column(String(100), default="general")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source_url", "title", name="uq_jobs_source_title"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(500))
    apply_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    apply_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_type: Mapped[str] = mapped_column(String(255), default="")
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(100), default="")
    work_mode: Mapped[str] = mapped_column(String(30), default="onsite")
    experience_level: Mapped[str] = mapped_column(String(30), default="fresher")
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class JobSkill(Base):
    __tablename__ = "job_skills"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
    is_required: Mapped[bool] = mapped_column(default=True)


class Suppression(Base):
    __tablename__ = "suppressions"
    email: Mapped[str] = mapped_column(String(320, collation="NOCASE"), primary_key=True)
    reason: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class StartupLead(Base):
    __tablename__ = "startup_leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    official_domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    website: Mapped[str] = mapped_column(String(500))
    industry: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    stage: Mapped[str] = mapped_column(String(100), default="")
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000))
    source_type: Mapped[str] = mapped_column(String(100), default="official_website")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    last_verified_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    notes: Mapped[str] = mapped_column(Text, default="")
    jobs: Mapped[list[StartupJob]] = relationship(back_populates="startup", cascade="all, delete-orphan")


class StartupJob(Base):
    __tablename__ = "startup_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startup_leads.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    department: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    employment_type: Mapped[str] = mapped_column(String(100), default="")
    posting_url: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(100), default="official_careers_page")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    startup: Mapped[StartupLead] = relationship(back_populates="jobs")


class Outreach(Base):
    __tablename__ = "outreach"
    __table_args__ = (UniqueConstraint("user_id", "contact_id", "job_id", name="uq_outreach_user_contact_job"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="drafted")
    drafted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user: Mapped[User] = relationship(back_populates="outreach")
    contact: Mapped[Contact] = relationship()
    resume: Mapped[ResumeAnalysis] = relationship()
    job: Mapped[Job] = relationship()
    messages: Mapped[list[Message]] = relationship(back_populates="outreach", cascade="all, delete-orphan")
    follow_ups: Mapped[list[FollowUp]] = relationship(back_populates="outreach", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    outreach_id: Mapped[int] = mapped_column(ForeignKey("outreach.id"), index=True)
    direction: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    gmail_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    outreach: Mapped[Outreach] = relationship(back_populates="messages")


class FollowUp(Base):
    __tablename__ = "follow_ups"
    id: Mapped[int] = mapped_column(primary_key=True)
    outreach_id: Mapped[int] = mapped_column(ForeignKey("outreach.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    outreach: Mapped[Outreach] = relationship(back_populates="follow_ups")


MATCH_JOBS_SQL = (PROJECT_ROOT / "db" / "match_jobs.sql").read_text(encoding="utf-8")


_firebase_store: FirebaseRealtimeStore | None = None


def firebase_store() -> FirebaseRealtimeStore:
    global _firebase_store
    if _firebase_store is None:
        _firebase_store = FirebaseRealtimeStore(
            settings.firebase_database_url,
            settings.firebase_service_account_path,
            settings.firebase_service_account_json,
        )
    return _firebase_store


def SessionLocal() -> Session:
    return create_firebase_session(Base, firebase_store())


def db_session():
    try:
        session = SessionLocal()
    except FirebaseStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(db_session)]
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/gmail.modify"]

app = FastAPI(title="Follow Up Fox API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class ContactCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    linkedin_url: str | None = Field(default=None, max_length=500)
    company: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=255)
    notes: str = Field(default="", max_length=3000)
    category: Literal["my", "employer"] = "my"

    @model_validator(mode="after")
    def require_contact_method(self):
        if not self.email and not self.linkedin_url:
            raise ValueError("email or linkedin_url is required")
        return self


class ContactResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str | None = None
    email_eligible: bool = False
    email_eligibility_reason: str | None = None
    linkedin_url: str | None = None
    company: str
    role: str
    notes: str = ""
    category: Literal["my", "employer"]
    location: str = ""
    hiring_roles: list[str] = Field(default_factory=list)
    connected: bool = False
    model_config = ConfigDict(from_attributes=True)


class DraftRequest(BaseModel):
    contact_id: int
    resume_summary: str = Field(default="", max_length=5000)
    tone: str = Field(default="warm and concise", max_length=100)
    previous_subject: str = Field(default="", max_length=255)
    previous_body: str = Field(default="", max_length=10000)
    follow_up: bool = False
    outreach_id: int | None = None


class DraftResponse(BaseModel):
    subject: str
    body: str
    outreach_id: int | None = None
    follow_up: bool = False
    gmail_draft_id: str | None = None
    resume_available: bool = False
    resume_filename: str | None = None


class LinkedInDraftRequest(BaseModel):
    contact_id: int
    tone: str = Field(default="warm and concise", max_length=100)


class SendRequest(BaseModel):
    outreach_id: int
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    attach_resume: bool = False
    follow_up: bool = False


class OutreachCreate(BaseModel):
    contact_id: int
    subject: str = Field(min_length=1, max_length=255)
    job_id: int | None = None
    resume_id: int | None = None
    body: str = Field(default="", max_length=10000)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class ProfileUpdate(BaseModel):
    professional_headline: str = Field(default="", max_length=500)
    about: str = Field(default="", max_length=5000)


class SettingsUpdate(BaseModel):
    human_approval_required: bool = True
    followup_delay_days: int = Field(default=5, ge=1, le=30)
    daily_send_limit: int = Field(default=20, ge=1, le=100)


async def current_web_context(question: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": question, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            response.raise_for_status()
            data = response.json()
        snippets = []
        if data.get("AbstractText"):
            snippets.append(data["AbstractText"])
        for topic in data.get("RelatedTopics", [])[:5]:
            if isinstance(topic, dict) and topic.get("Text"):
                snippets.append(topic["Text"])
        if snippets:
            source = data.get("AbstractURL") or "DuckDuckGo search results"
            return f"Current web context (source: {source}): " + " ".join(snippets[:3])
    except (httpx.HTTPError, TypeError, ValueError):
        return ""
    return ""


def matching_jobs(db: Session, resume_id: int, user_id: int) -> list[dict[str, object]]:
    rows = db.execute(
        text(MATCH_JOBS_SQL),
        {"resume_id": resume_id, "user_id": user_id},
    ).mappings()
    return [dict(row) for row in rows]


def calculate_match_scores(
    job_matches: list[dict[str, object]],
) -> tuple[int, dict[str, int | None], dict[str, int]]:
    level_groups = {
        "intern": {"intern"},
        "entry": {"fresher", "junior"},
        "mid": {"mid"},
        "senior": {"senior"},
    }
    all_scores = [float(job["skill_match_percentage"]) for job in job_matches]
    level_fit: dict[str, int | None] = {}
    level_fit_counts: dict[str, int] = {}

    for level, experience_levels in level_groups.items():
        scores = [
            float(job["skill_match_percentage"])
            for job in job_matches
            if job["experience_level"] in experience_levels
        ]
        level_fit[level] = round(mean(scores)) if scores else None
        level_fit_counts[level] = len(scores)

    return round(mean(all_scores)) if all_scores else 0, level_fit, level_fit_counts


def employer_payload(job_matches: list[dict[str, object]]) -> list[dict[str, object]]:
    employers: dict[str, dict[str, object]] = {}
    for match in job_matches:
        company = str(match["company_name"])
        employer = employers.setdefault(company, {
            "id": match["job_id"],
            "contact_id": match["contact_id"],
            "company": company,
            "location": match["company_location"],
            "website": "",
            "industry": "",
            "hiring_roles": [],
            "skills": [],
            "contact_name": match["hr_name"],
            "contact_email": (
                match["hr_email"]
                if match["hr_email"] and not str(match["hr_email"]).lower().endswith(".invalid")
                else None
            ),
            "contact_eligible": bool(
                (match["hr_email"] and not str(match["hr_email"]).lower().endswith(".invalid"))
                or match.get("contact_linkedin")
            ),
            "email_eligible": bool(
                match["hr_email"] and not str(match["hr_email"]).lower().endswith(".invalid")
            ),
            "contact_linkedin": match.get("contact_linkedin") or "",
            "notes": "",
            "match_score": match["skill_match_percentage"],
            "jobs": [],
        })
        if match["position"] not in employer["hiring_roles"]:
            employer["hiring_roles"].append(match["position"])
        employer["jobs"].append(match)
        employer["match_score"] = max(
            int(employer["match_score"]),
            int(match["skill_match_percentage"]),
        )
    return list(employers.values())


async def ai_skills(text: str) -> list[str]:
    if not settings.groq_api_key:
        return fallback_skills(text)
    try:
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                'Extract only concrete professional skills from resumes. Return JSON exactly as {{"skills":["skill"]}}.',
            ),
            ("human", "{resume_text}"),
        ])
        llm = ChatGroq(
            model=settings.groq_model,
            temperature=0,
            api_key=settings.groq_api_key,
            timeout=30,
        )
        response = await (prompt | llm).ainvoke({"resume_text": text[:20000]})
        content = message_text(response)
        return parse_skill_response(content) or fallback_skills(text)
    except (GroqError, KeyError, TypeError, ValueError):
        return fallback_skills(text)


def message_text(response: AIMessage) -> str:
    if isinstance(response.content, str):
        return response.content
    if isinstance(response.content, list):
        return "".join(
            item["text"]
            for item in response.content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    raise TypeError("The Groq response did not contain text")


@app.post("/resume/analyze")
async def analyze_resume(request: Request, db: DbSession, file: UploadFile = File(...)):
    user = current_user(request, db)
    filename = Path((file.filename or "resume.txt").replace("\\", "/")).name
    file_content = await file.read(MAX_RESUME_BYTES + 1)
    if len(file_content) > MAX_RESUME_BYTES:
        raise HTTPException(status_code=413, detail="Resume file must be 10 MB or smaller")
    try:
        text = extract_resume_text(file_content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    skills = await ai_skills(text)
    for resume in db.scalars(select(ResumeAnalysis).where(ResumeAnalysis.user_id == user.id)):
        resume.is_active = False
    analysis = ResumeAnalysis(
        user_id=user.id,
        filename=filename,
        extracted_text=text,
        skills=json.dumps(skills),
        file_content=fernet.encrypt(file_content),
        is_active=True,
        parsed_at=datetime.now(timezone.utc),
    )
    db.add(analysis)
    db.flush()
    for skill_name in skills:
        skill = db.scalar(select(Skill).where(func.lower(Skill.name) == skill_name.lower()))
        if not skill:
            skill = Skill(name=skill_name, category="resume")
            db.add(skill)
            db.flush()
        db.add(ResumeSkill(resume_id=analysis.id, skill_id=skill.id, confidence=1.0))
    db.commit()
    job_matches = matching_jobs(db, analysis.id, user.id)
    employers = employer_payload(job_matches)
    overall_score, level_fit, level_fit_counts = calculate_match_scores(job_matches)
    gaps = [
        skill.name for skill in db.scalars(select(Skill).order_by(Skill.name))
        if skill.name.lower() not in {name.lower() for name in skills}
    ][:6]
    improvement_actions = [
        f"Add one measurable project outcome demonstrating {gap}." for gap in gaps[:3]
    ] or ["Add measurable outcomes, relevant tools, and project examples to strengthen your resume."]
    detailed_improvements = [
        f"Add a concise project or work example showing how you used {gap}, including your action and measurable result."
        for gap in gaps
    ] or ["Tailor your headline to the target role, quantify project outcomes, and place your strongest relevant skills near the top."]
    summary = (
        f"Your resume demonstrates {len(skills)} identified professional skills and matches "
        f"{len(employers)} employers in the current employer database."
        if skills else
        "No supported professional skills were identified. Add clearer skills, tools, and project outcomes to your resume."
    )
    return {
        "analysis_id": analysis.id,
        "filename": filename,
        "resume_available": True,
        "resume_filename": filename,
        "resume_available": True,
        "resume_filename": filename,
        "skills": skills,
        "employers": employers,
        "jobs": job_matches,
        "summary": summary,
        "score": overall_score,
        "level_fit": level_fit,
        "level_fit_counts": level_fit_counts,
        "strengths": skills[:6],
        "gaps": gaps,
        "improvement_actions": improvement_actions,
        "detailed_improvements": detailed_improvements,
    }


@app.put("/profile")
def update_profile(payload: ProfileUpdate, request: Request, db: DbSession):
    user = current_user(request, db)
    user.professional_headline = payload.professional_headline
    user.about = payload.about
    db.commit()
    return {"professional_headline": user.professional_headline, "about": user.about}


@app.get("/resume/latest")
def latest_resume(request: Request, db: DbSession):
    user = current_user(request, db)
    analysis = db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id, ResumeAnalysis.is_active.is_(True))
        .order_by(ResumeAnalysis.id.desc())
    )
    if not analysis:
        return {
            "analysis_id": None,
            "skills": [],
            "employers": [],
            "level_fit": {},
            "level_fit_counts": {},
            "resume_available": False,
            "resume_filename": None,
        }
    try:
        skills = json.loads(analysis.skills or "[]")
    except (TypeError, ValueError):
        skills = []
    job_matches = matching_jobs(db, analysis.id, user.id)
    employers = employer_payload(job_matches)
    overall_score, level_fit, level_fit_counts = calculate_match_scores(job_matches)
    return {
        "analysis_id": analysis.id,
        "filename": analysis.filename,
        "resume_available": analysis.file_content is not None,
        "resume_filename": analysis.filename if analysis.file_content is not None else None,
        "skills": skills,
        "employers": employers,
        "jobs": job_matches,
        "summary": (
            f"Your resume demonstrates {len(skills)} identified professional skills and matches "
            f"{len(employers)} employers in the current employer database."
        ),
        "score": overall_score,
        "level_fit": level_fit,
        "level_fit_counts": level_fit_counts,
    }


@app.get("/resume/latest/file")
def latest_resume_file(request: Request, db: DbSession):
    user = current_user(request, db)
    analysis = db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id, ResumeAnalysis.is_active.is_(True))
        .order_by(ResumeAnalysis.id.desc())
    )
    if not analysis or not analysis.file_content:
        raise HTTPException(status_code=404, detail="No saved resume file is available")
    try:
        file_content = fernet.decrypt(analysis.file_content)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=500,
            detail="The saved resume could not be decrypted. Upload it again to review it.",
        ) from exc
    filename = Path(analysis.filename.replace("\\", "/")).name
    content_type, _ = mimetypes.guess_type(filename)
    return Response(
        content=file_content,
        media_type=content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/profile")
def profile(request: Request, db: DbSession):
    user = current_user(request, db)
    return {"professional_headline": user.professional_headline, "about": user.about}


@app.get("/settings")
def get_settings(request: Request, db: DbSession):
    user = current_user(request, db)
    return {
        "human_approval_required": user.human_approval_required,
        "followup_delay_days": user.followup_delay_days,
        "daily_send_limit": user.daily_send_limit,
    }


@app.put("/settings")
def update_settings(payload: SettingsUpdate, request: Request, db: DbSession):
    user = current_user(request, db)
    user.human_approval_required = payload.human_approval_required
    user.followup_delay_days = payload.followup_delay_days
    user.daily_send_limit = payload.daily_send_limit
    db.commit()
    return get_settings(request, db)


@app.get("/employers")
def list_matched_employers(request: Request, db: DbSession):
    user = current_user(request, db)
    analysis = db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id, ResumeAnalysis.is_active.is_(True))
        .order_by(ResumeAnalysis.id.desc())
    )
    if not analysis:
        return {"skills": [], "employers": [], "jobs": []}
    try:
        skills = json.loads(analysis.skills)
    except (TypeError, ValueError):
        skills = []
    job_matches = matching_jobs(db, analysis.id, user.id)
    return {
        "analysis_id": analysis.id,
        "filename": analysis.filename,
        "skills": skills,
        "employers": employer_payload(job_matches),
        "jobs": job_matches,
    }


@app.get("/jobs/matches")
def list_job_matches(request: Request, db: DbSession):
    user = current_user(request, db)
    resume = db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id, ResumeAnalysis.is_active.is_(True))
        .order_by(ResumeAnalysis.id.desc())
    )
    if not resume:
        return {"resume_id": None, "jobs": []}
    return {"resume_id": resume.id, "jobs": matching_jobs(db, resume.id, user.id)}


@app.get("/startups")
def list_startups(request: Request, db: DbSession):
    current_user(request, db)
    startups = db.scalars(select(StartupLead).order_by(StartupLead.last_verified_at.desc())).all()
    return [
        {
            "id": startup.id,
            "company": startup.company,
            "official_domain": startup.official_domain,
            "website": startup.website,
            "industry": startup.industry,
            "location": startup.location,
            "stage": startup.stage,
            "contact_email": startup.contact_email,
            "linkedin_url": startup.linkedin_url,
            "contact_category": startup.contact_category,
            "source_url": startup.source_url,
            "source_type": startup.source_type,
            "confidence": startup.confidence,
            "review_status": startup.review_status,
            "last_verified_at": startup.last_verified_at,
            "jobs": [
                {
                    "id": job.id,
                    "title": job.title,
                    "department": job.department,
                    "location": job.location,
                    "employment_type": job.employment_type,
                    "posting_url": job.posting_url,
                    "source_type": job.source_type,
                    "posted_at": job.posted_at,
                    "status": job.status,
                    "last_seen_at": job.last_seen_at,
                }
                for job in startup.jobs
            ],
        }
        for startup in startups
    ]


def set_session(response: Response, user_id: int):
    response.set_cookie(
    "outreach_session",
    signer.dumps({"user_id": user_id}),
    httponly=True,
    samesite="none",
    secure=True,
    max_age=604800
)


def current_user(request: Request, db: Session) -> User:
    value = request.cookies.get("outreach_session")
    if not value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in with Google to continue")
    try:
        user_id = signer.loads(value, max_age=604800)["user_id"]
    except BadSignature as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired") from exc
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account not found")
    return user


def google_flow(state: str | None = None) -> Flow:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    client_config = {"web": {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [settings.google_redirect_uri]}}
    flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state)
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def google_credentials(user: User) -> Credentials:
    if not user.refresh_token:
        raise HTTPException(status_code=409, detail="Connect Google to use Gmail.")
    credentials = Credentials(token=None, refresh_token=fernet.decrypt(user.refresh_token.encode()).decode(), token_uri="https://oauth2.googleapis.com/token", client_id=settings.google_client_id, client_secret=settings.google_client_secret, scopes=SCOPES)
    if not credentials.valid:
        try:
            credentials.refresh(GoogleRequest())
        except RefreshError as exc:
            raise HTTPException(
                status_code=409,
                detail="Google authorization has expired or is missing Gmail permission. Reconnect Google and grant Gmail access.",
            ) from exc
    return credentials


@app.get("/health")
def health():
    return {"status": "ok", "service": "outreachos-api"}


@app.get("/auth/google/login")
def google_login():
    flow = google_flow()
    url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    response = RedirectResponse(url)
    response.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return response


@app.get("/auth/google/callback")
def google_callback(request: Request, db: DbSession):
    google_error = request.query_params.get("error")
    if google_error:
        detail = request.query_params.get("error_description") or google_error
        raise HTTPException(status_code=400, detail=f"Google authorization was not completed: {detail}")
    state = request.cookies.get("oauth_state")
    if not state:
        raise HTTPException(status_code=400, detail="OAuth session expired. Start Google sign-in again.")
    flow = google_flow(state)
    try:
        # OAuthlib checks this environment variable when parsing the callback
        # and again while exchanging the code. Set it at the exact boundary
        # so a stale reload worker cannot retain the previous value.
        if settings.google_redirect_uri.startswith(("http://localhost", "http://127.0.0.1")):
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        flow.fetch_token(authorization_response=str(request.url))
    except (ValueError, OAuth2Error, requests.RequestException) as exc:
        raise HTTPException(status_code=400, detail=f"Google authorization failed: {exc}") from exc
    credentials = flow.credentials
    try:
        identity = None
        if credentials.id_token:
            raw_identity = credentials.id_token
            if isinstance(raw_identity, dict):
                identity = raw_identity
            elif isinstance(raw_identity, str):
                try:
                    identity = id_token.verify_oauth2_token(raw_identity, GoogleRequest(), settings.google_client_id)
                except ValueError:
                    identity = None
        if not isinstance(identity, dict) or "sub" not in identity or "email" not in identity:
            if not credentials.token:
                raise HTTPException(status_code=400, detail="Google did not return an access token for identity lookup")
            userinfo = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=10,
            )
            userinfo.raise_for_status()
            identity = userinfo.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail="Google identity lookup failed. Check the granted OAuth scopes and try again.") from exc
    if not isinstance(identity, dict) or "sub" not in identity or "email" not in identity:
        raise HTTPException(status_code=400, detail="Google did not return the required account email and subject claims")
    user = db.scalar(select(User).where(User.google_id == identity["sub"]))
    if not user:
        user = db.scalar(select(User).where(User.email == identity["email"]))
        if user:
            user.google_id = identity["sub"]
        else:
            user = User(google_id=identity["sub"], email=identity["email"], name=identity.get("name", identity["email"]))
            db.add(user)
    user.email = identity["email"]
    user.name = identity.get("name", identity["email"])
    if credentials.refresh_token:
        user.refresh_token = fernet.encrypt(credentials.refresh_token.encode()).decode()
        user.gmail_connected = True
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This Google account is already linked to another user") from exc
    response = RedirectResponse(f"{settings.frontend_url}/?signed_in=1")
    set_session(response, user.id)
    response.delete_cookie("oauth_state")
    return response


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("outreach_session", samesite="none", secure=True)
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request, db: DbSession):
    user = current_user(request, db)
    return {"id": user.id, "name": user.name, "email": user.email, "google_connected": bool(user.refresh_token)}


@app.get("/dashboard/stats")
def dashboard_stats(request: Request, db: DbSession):
    user = current_user(request, db)
    today = datetime.now(timezone.utc).date()
    visit = db.scalar(
        select(UserVisit).where(
            UserVisit.user_id == user.id,
            UserVisit.visit_date == datetime.combine(today, datetime.min.time()),
        )
    )
    if not visit:
        db.add(UserVisit(user_id=user.id, visit_date=datetime.combine(today, datetime.min.time())))
        db.commit()
    visitor_since = datetime.combine(today - timedelta(days=29), datetime.min.time())
    visitor_count = db.scalar(
        select(func.count(UserVisit.id)).where(
            UserVisit.user_id == user.id,
            UserVisit.visit_date >= visitor_since,
        )
    ) or 0
    outreach = db.scalars(select(Outreach).where(Outreach.user_id == user.id)).all()
    total = len(outreach)
    replied = sum(row.status == "replied" for row in outreach)
    sent = sum(row.status in {"sent", "replied"} for row in outreach)
    awaiting = sum(row.status == "sent" for row in outreach)
    drafts = sum(row.status == "drafted" for row in outreach)
    daily_sent = sum(row.sent_at is not None and row.sent_at.date() == today for row in outreach)
    activity = []
    for offset in range(29, -1, -1):
        day = today - timedelta(days=offset)
        activity.append({"date": day.isoformat(), "count": sum(row.sent_at is not None and row.sent_at.date() == day for row in outreach)})
    return {
        "total_outreach": total,
        "companies_applied": total,
        "companies_responded": replied,
        "awaiting_reply": awaiting,
        "response_rate": round(replied / sent * 100, 1) if sent else 0,
        "daily_sent": daily_sent,
        "daily_limit": user.daily_send_limit,
        "visitors": visitor_count,
        "breakdown": {"replied": replied, "awaiting": awaiting, "drafts": drafts, "no_response": max(0, sent - replied - awaiting)},
        "activity": activity,
    }


@app.get("/contacts", response_model=list[ContactResponse])
def list_contacts(request: Request, db: DbSession, category: Literal["my", "employer", "all"] = "all"):
    user = current_user(request, db)
    query = select(Contact).where(
        or_(Contact.owner_user_id == user.id, Contact.owner_user_id.is_(None))
    )
    if category != "all":
        query = query.where(Contact.category == category)
    contacts = list(db.scalars(query.order_by(Contact.id.desc())))
    company_ids = {contact.company_id for contact in contacts}
    if not company_ids:
        return []
    locations = dict(db.execute(
        select(Company.id, Company.hq_city).where(Company.id.in_(company_ids))
    ).all())
    hiring_roles: dict[int, list[str]] = {}
    for company_id, title in db.execute(
        select(Job.company_id, Job.title)
        .where(Job.company_id.in_(company_ids), Job.is_active.is_(True))
        .order_by(Job.company_id, Job.title)
    ):
        roles = hiring_roles.setdefault(company_id, [])
        if title not in roles:
            roles.append(title)
    contact_responses = []
    for contact in contacts:
        email_eligibility_reason = email_outreach_block_reason(db, contact)
        contact_responses.append({
            "id": contact.id,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "email": contact.email if not contact.email.lower().endswith(".invalid") else None,
            "email_eligible": email_eligibility_reason is None,
            "email_eligibility_reason": email_eligibility_reason,
            "linkedin_url": contact.linkedin_url,
            "company": contact.company,
            "role": contact.role,
            "notes": contact.notes,
            "category": contact.category,
            "location": locations.get(contact.company_id, ""),
            "hiring_roles": hiring_roles.get(contact.company_id, []),
            "connected": contact.owner_user_id == user.id,
        })
    return contact_responses


@app.post("/contacts/{contact_id}/connect")
def connect_contact(contact_id: int, request: Request, db: DbSession):
    user = current_user(request, db)
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Employer contact not found")
    if contact.owner_user_id == user.id:
        return {"id": contact.id, "connected": True}
    if contact.owner_user_id is not None:
        raise HTTPException(status_code=409, detail="This contact is already connected to another account")
    if contact.category != "employer":
        raise HTTPException(status_code=404, detail="Employer contact not found")

    contact.owner_user_id = user.id
    contact.category = "my"
    db.commit()
    return {"id": contact.id, "connected": True}


def email_outreach_block_reason(db: Session, contact: Contact) -> str | None:
    if not contact.email or contact.email.lower().endswith(".invalid"):
        return "This contact does not have a usable email address."
    if contact.opted_out:
        return "This contact opted out of email."
    if db.get(Suppression, contact.email):
        return "This email address is suppressed."
    return None


def attach_resume_file(message: EmailMessage, resume: ResumeAnalysis, user_id: int) -> None:
    if resume.user_id != user_id:
        raise HTTPException(status_code=404, detail="Resume not found")
    if not resume.file_content:
        raise HTTPException(
            status_code=409,
            detail="This resume was uploaded before file attachments were enabled. Upload it again to attach it.",
        )
    try:
        file_content = fernet.decrypt(resume.file_content)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=500,
            detail="The saved resume could not be decrypted. Upload it again before attaching it.",
        ) from exc
    filename = Path(resume.filename.replace("\\", "/")).name
    content_type, _ = mimetypes.guess_type(filename)
    maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
    message.add_attachment(
        file_content,
        maintype=maintype,
        subtype=subtype,
        filename=filename,
    )


@app.post("/contacts", response_model=ContactResponse, status_code=201)
def create_contact(payload: ContactCreate, request: Request, db: DbSession):
    user = current_user(request, db)
    company_name = payload.company.strip()
    domain_hash = hashlib.sha256(company_name.casefold().encode("utf-8")).hexdigest()[:16]
    domain = f"manual-{domain_hash}.invalid"
    company = db.scalar(select(Company).where(Company.domain == domain))
    if not company:
        company = db.scalar(
            select(Company)
            .where(func.lower(Company.name) == company_name.casefold())
            .order_by(Company.id)
        )
    if not company:
        company = Company(
            name=company_name,
            domain=domain,
            industry="Unknown",
            size_band="unknown",
            hq_city="Unknown",
        )
        db.add(company)
        db.flush()
    email = str(payload.email).lower() if payload.email else None
    if not email:
        address_hash = hashlib.sha256(
            f"{user.id}:{payload.first_name}:{payload.last_name}:{company.id}".encode("utf-8")
        ).hexdigest()[:16]
        email = f"no-contact+{address_hash}@example.invalid"
    contact = Contact(
        company_id=company.id,
        owner_user_id=user.id,
        full_name=f"{payload.first_name} {payload.last_name}".strip(),
        first_name=payload.first_name,
        last_name=payload.last_name,
        job_title=payload.role,
        role=payload.role,
        company=company_name,
        email=email,
        linkedin_url=payload.linkedin_url,
        notes=payload.notes,
        category=payload.category,
        source="user_added",
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    response_data = ContactResponse.model_validate(contact).model_dump()
    response_data["location"] = company.hq_city
    response_data["connected"] = contact.owner_user_id == user.id
    response_data["email"] = (
        contact.email if not contact.email.lower().endswith(".invalid") else None
    )
    response_data["email_eligible"] = email_outreach_block_reason(db, contact) is None
    response_data["email_eligibility_reason"] = email_outreach_block_reason(db, contact)
    response_data["hiring_roles"] = list(dict.fromkeys(db.scalars(
        select(Job.title)
        .where(Job.company_id == company.id, Job.is_active.is_(True))
        .order_by(Job.title)
    )))
    return response_data


def visible_contact(db: Session, user_id: int, contact_id: int) -> Contact | None:
    return db.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            or_(Contact.owner_user_id == user_id, Contact.owner_user_id.is_(None)),
        )
    )


@app.post("/drafts/suggest", response_model=DraftResponse)
async def suggest_draft(payload: DraftRequest, request: Request, db: DbSession):
    user = current_user(request, db)
    contact = visible_contact(db, user.id, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    follow_up_outreach = None
    if payload.follow_up:
        if payload.outreach_id is None:
            raise HTTPException(status_code=400, detail="Choose an outreach record to follow up.")
        follow_up_outreach = db.scalar(
            select(Outreach).where(
                Outreach.id == payload.outreach_id,
                Outreach.user_id == user.id,
                Outreach.contact_id == contact.id,
            )
        )
        if not follow_up_outreach or follow_up_outreach.status != "sent":
            raise HTTPException(status_code=409, detail="This outreach is not awaiting a follow-up.")
    subject_options = [
        f"A thought on {contact.company}'s approach to product craft",
        f"Exploring opportunities at {contact.company}",
        f"A quick introduction from {user.name}",
    ]
    if follow_up_outreach:
        subject_options = [
            follow_up_outreach.subject
            if follow_up_outreach.subject.lower().startswith("re:")
            else f"Re: {follow_up_outreach.subject}"
        ]
    latest_resume = db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id, ResumeAnalysis.is_active.is_(True))
        .order_by(ResumeAnalysis.id.desc())
    )
    resume_context = payload.resume_summary or (latest_resume.extracted_text[:3000] if latest_resume else "")
    resume_available = bool(latest_resume and latest_resume.file_content)
    resume_filename = latest_resume.filename if resume_available else None
    background = resume_context[:300].strip()
    body_options = [
        f"Hi {contact.first_name},\n\nI've been following {contact.company}'s work and would love to learn more about your team. {background}\n\nWould you be open to a short conversation sometime next week?\n\nBest,\n{user.name}",
        f"Hi {contact.first_name},\n\nI'm reaching out to learn more about your work as {contact.role} at {contact.company}. {background}\n\nIf you have time, I'd appreciate the chance to hear about your team's priorities.\n\nBest,\n{user.name}",
        f"Hi {contact.first_name},\n\nI'm interested in the work happening at {contact.company} and thought I'd introduce myself. {background}\n\nWould you be open to a brief conversation about your experience there?\n\nBest,\n{user.name}",
    ]
    subject, body = next(
        (
            (subject_option, body_option)
            for subject_option, body_option in zip(subject_options, body_options)
            if subject_option != payload.previous_subject or body_option != payload.previous_body
        ),
        (subject_options[0], body_options[0]),
    )
    if follow_up_outreach:
        subject = subject_options[0]
        body = (
            f"Hi {contact.first_name},\n\n"
            f"I wanted to follow up on my previous message about {contact.company}. "
            "I understand you may be busy, but I would still appreciate the opportunity "
            "to connect if you have a few minutes.\n\n"
            f"Best,\n{user.name}"
        )
    if settings.groq_api_key:
        try:
            prompt = ChatPromptTemplate.from_messages([
                (
                    "system",
                    (
                        "Write a concise, polite follow-up email in the existing conversation. "
                        "Return only valid JSON with exactly two string fields: subject and body. "
                        "Refer naturally to the earlier message without inventing facts."
                        if follow_up_outreach
                        else "Write a warm, specific, concise networking email. Return only valid JSON with exactly two string fields: subject and body. Do not invent facts about the recipient or company. Mention the candidate's relevant background only if supplied. If a previous draft is provided, create a clearly different alternative rather than repeating or lightly paraphrasing it."
                    ),
                ),
                (
                    "human",
                    "Candidate name: {candidate_name}. Candidate resume context: {resume_context}. "
                    "Recipient: {recipient_name}, role: {recipient_role}, company: {company}. "
                    "Contact notes: {contact_notes}. Tone: {tone}. "
                    "Previous draft to avoid: {previous_draft}. "
                    "Original message subject and body for follow-up: {original_message}.",
                ),
            ])
            llm = ChatGroq(
                model=settings.groq_model,
                temperature=0.7,
                api_key=settings.groq_api_key,
                timeout=25,
            ).bind(response_format={"type": "json_object"})
            response = await (prompt | llm).ainvoke({
                "candidate_name": user.name,
                "resume_context": resume_context[:3000] or "not supplied",
                "recipient_name": f"{contact.first_name} {contact.last_name}",
                "recipient_role": contact.role,
                "company": contact.company,
                "contact_notes": contact.notes or "none",
                "tone": payload.tone,
                "previous_draft": (
                    f"Subject: {payload.previous_subject}\n{payload.previous_body}"
                    if payload.previous_subject or payload.previous_body
                    else "none"
                ),
                "original_message": (
                    f"Subject: {follow_up_outreach.subject}\n{follow_up_outreach.body}"
                    if follow_up_outreach
                    else "none"
                ),
            })
            parsed = json.loads(message_text(response))
            if isinstance(parsed, dict) and isinstance(parsed.get("subject"), str) and isinstance(parsed.get("body"), str):
                subject = parsed["subject"].strip() or subject
                body = parsed["body"].strip() or body
        except (GroqError, KeyError, TypeError, ValueError):
            pass
    if follow_up_outreach:
        return DraftResponse(
            subject=subject,
            body=body,
            outreach_id=follow_up_outreach.id,
            follow_up=True,
        )
    if not latest_resume:
        return DraftResponse(subject=subject, body=body)
    job = db.scalar(
        select(Job)
        .where(Job.contact_id == contact.id, Job.is_active.is_(True))
        .order_by(Job.posted_at.desc())
    )
    if not job:
        return DraftResponse(
            subject=subject,
            body=body,
            resume_available=resume_available,
            resume_filename=resume_filename,
        )
    existing = db.scalar(
        select(Outreach).where(
            Outreach.user_id == user.id,
            Outreach.contact_id == contact.id,
            Outreach.job_id == job.id,
        )
    )
    if not existing:
        drafted_today = db.scalar(
            select(func.count(Outreach.id)).where(
                Outreach.user_id == user.id,
                Outreach.status == "drafted",
                Outreach.drafted_at >= datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
            )
        ) or 0
        if drafted_today >= user.daily_draft_limit:
            raise HTTPException(status_code=429, detail="Daily email draft limit reached")
        existing = Outreach(
            user_id=user.id,
            resume_id=latest_resume.id,
            contact_id=contact.id,
            job_id=job.id,
            subject=subject,
            body=body,
            status="drafted",
        )
        db.add(existing)
    else:
        if existing.status != "drafted":
            raise HTTPException(status_code=409, detail="This job already has an outreach record")
        existing.resume_id = latest_resume.id
        existing.subject = subject
        existing.body = body
    db.flush()
    gmail_draft_id = existing.gmail_draft_id
    if user.refresh_token and email_outreach_block_reason(db, contact) is None:
        credentials = google_credentials(user)
        gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        message = EmailMessage()
        message["To"], message["Subject"] = contact.email, subject
        message["From"] = user.email
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft_message = {"message": {"raw": raw}}
        if gmail_draft_id:
            saved_draft = gmail.users().drafts().update(
                userId="me", id=gmail_draft_id, body=draft_message
            ).execute()
        else:
            saved_draft = gmail.users().drafts().create(
                userId="me", body=draft_message
            ).execute()
        gmail_draft_id = saved_draft.get("id")
        existing.gmail_draft_id = gmail_draft_id
        existing.thread_id = saved_draft.get("message", {}).get("threadId")
    db.commit()
    return DraftResponse(
        subject=subject,
        body=body,
        outreach_id=existing.id,
        gmail_draft_id=gmail_draft_id,
        resume_available=resume_available,
        resume_filename=resume_filename,
    )


@app.post("/drafts/linkedin", response_model=DraftResponse)
async def suggest_linkedin_draft(payload: LinkedInDraftRequest, request: Request, db: DbSession):
    user = current_user(request, db)
    contact = visible_contact(db, user.id, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    body = (
        f"Hi {contact.first_name}, I’m interested in the work your team is doing at "
        f"{contact.company}. I’d enjoy connecting and learning about your experience as "
        f"{contact.role}. Thanks, {user.name}"
    )
    if settings.groq_api_key:
        try:
            prompt = ChatPromptTemplate.from_messages([
                (
                    "system",
                    "Write a concise, genuine LinkedIn connection message under 300 characters. Do not invent facts.",
                ),
                (
                    "human",
                    "Candidate: {candidate_name}. Recipient: {recipient_name}, {recipient_role} "
                    "at {company}. Tone: {tone}.",
                ),
            ])
            llm = ChatGroq(
                model=settings.groq_model,
                temperature=0.7,
                api_key=settings.groq_api_key,
                timeout=25,
            )
            response = await (prompt | llm).ainvoke({
                "candidate_name": user.name,
                "recipient_name": f"{contact.first_name} {contact.last_name}",
                "recipient_role": contact.role,
                "company": contact.company,
                "tone": payload.tone,
            })
            generated = message_text(response).strip()
            if generated:
                body = generated
        except (GroqError, KeyError, TypeError, ValueError):
            pass
    return DraftResponse(subject="LinkedIn connection message", body=body)


@app.post("/outreach", status_code=201)
def create_outreach(payload: OutreachCreate, request: Request, db: DbSession):
    user = current_user(request, db)
    contact = visible_contact(db, user.id, payload.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    resume = db.get(ResumeAnalysis, payload.resume_id) if payload.resume_id else db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id, ResumeAnalysis.is_active.is_(True))
        .order_by(ResumeAnalysis.id.desc())
    )
    if not resume or resume.user_id != user.id:
        raise HTTPException(status_code=400, detail="Upload a resume before creating outreach")
    job_query = select(Job).where(
        Job.contact_id == contact.id,
        Job.is_active.is_(True),
    )
    if payload.job_id is not None:
        job_query = job_query.where(Job.id == payload.job_id)
    job = db.scalar(job_query.order_by(Job.posted_at.desc()))
    if not job:
        raise HTTPException(status_code=400, detail="No active job is associated with this contact")
    existing = db.scalar(
        select(Outreach).where(
            Outreach.user_id == user.id,
            Outreach.contact_id == contact.id,
            Outreach.job_id == job.id,
        )
    )
    if existing and existing.status != "drafted":
        raise HTTPException(status_code=409, detail="This job already has an outreach record")
    outreach = existing or Outreach(
        user_id=user.id,
        resume_id=resume.id,
        contact_id=contact.id,
        job_id=job.id,
        subject=payload.subject,
        body=payload.body,
        status="drafted",
    )
    outreach.resume_id = resume.id
    outreach.subject = payload.subject
    if payload.body:
        outreach.body = payload.body
    if existing is None:
        db.add(outreach)
    db.commit()
    db.refresh(outreach)
    return {"id": outreach.id, "contact_id": outreach.contact_id, "subject": outreach.subject, "status": outreach.status}


@app.get("/outreach")
def list_outreach(request: Request, db: DbSession):
    user = current_user(request, db)
    rows = db.scalars(select(Outreach).where(Outreach.user_id == user.id).order_by(Outreach.id.desc())).all()
    return [{"id": row.id, "contact_id": row.contact_id, "subject": row.subject, "status": row.status, "sent_at": row.sent_at} for row in rows]


@app.post("/outreach/send")
def send_outreach(payload: SendRequest, request: Request, db: DbSession):
    user = current_user(request, db)
    outreach = db.scalar(select(Outreach).where(Outreach.id == payload.outreach_id, Outreach.user_id == user.id))
    if not outreach:
        raise HTTPException(status_code=404, detail="Outreach not found")
    if payload.follow_up and outreach.status != "sent":
        raise HTTPException(status_code=409, detail="This outreach is not awaiting a follow-up.")
    if not payload.follow_up and outreach.status != "drafted":
        raise HTTPException(status_code=409, detail="This outreach has already been sent.")
    contact = db.get(Contact, outreach.contact_id)
    block_reason = email_outreach_block_reason(db, contact)
    if block_reason:
        raise HTTPException(status_code=409, detail=block_reason)
    resume = None
    if payload.attach_resume:
        resume = db.get(ResumeAnalysis, outreach.resume_id)
        if not resume:
            raise HTTPException(status_code=409, detail="The resume for this draft is no longer available.")
        if not resume.file_content:
            raise HTTPException(
                status_code=409,
                detail="This resume was uploaded before file attachments were enabled. Upload it again to attach it.",
            )
    credentials = google_credentials(user)
    gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    message = EmailMessage()
    message["To"], message["Subject"] = contact.email, payload.subject
    message["From"] = user.email
    message.set_content(payload.body)
    if resume:
        attach_resume_file(message, resume, user.id)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    if payload.follow_up:
        send_body = {"raw": raw}
        if outreach.thread_id:
            send_body["threadId"] = outreach.thread_id
        result = gmail.users().messages().send(userId="me", body=send_body).execute()
    elif outreach.gmail_draft_id:
        gmail.users().drafts().update(
            userId="me",
            id=outreach.gmail_draft_id,
            body={"message": {"raw": raw}},
        ).execute()
        result = gmail.users().drafts().send(
            userId="me", body={"id": outreach.gmail_draft_id}
        ).execute()
    else:
        result = gmail.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": outreach.thread_id},
        ).execute()
    outreach.status, outreach.subject, outreach.body = "sent", payload.subject, payload.body
    outreach.sent_at = datetime.now(timezone.utc)
    outreach.follow_up_due = outreach.sent_at + timedelta(days=user.followup_delay_days)
    outreach.thread_id = result.get("threadId") or result.get("message", {}).get("threadId")
    db.add(Message(outreach_id=outreach.id, direction="sent", body=payload.body, gmail_message_id=result.get("id")))
    follow_up = db.scalar(
        select(FollowUp).where(
            FollowUp.outreach_id == outreach.id,
            FollowUp.status == "scheduled",
        )
    )
    if follow_up is None:
        follow_up = FollowUp(outreach_id=outreach.id, due_at=outreach.follow_up_due)
        db.add(follow_up)
    else:
        follow_up.due_at = outreach.follow_up_due
    db.commit()
    return {
        "status": "sent",
        "message_id": result.get("id"),
        "thread_id": result.get("threadId"),
        "follow_up_due": outreach.follow_up_due,
    }


@app.get("/calendar/events")
def list_calendar_events(start: datetime, end: datetime, request: Request, db: DbSession):
    user = current_user(request, db)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="Calendar range must include timezone information.")
    if end <= start:
        raise HTTPException(status_code=422, detail="Calendar range end must be after its start.")
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    follow_ups = db.scalars(
        select(FollowUp)
        .join(Outreach, FollowUp.outreach_id == Outreach.id)
        .where(
            Outreach.user_id == user.id,
            FollowUp.status == "scheduled",
            FollowUp.due_at >= start_utc,
            FollowUp.due_at < end_utc,
        )
        .order_by(FollowUp.due_at)
    ).all()
    events = []
    for follow_up in follow_ups:
        outreach = follow_up.outreach
        contact = outreach.contact if outreach else None
        due_at = follow_up.due_at.replace(tzinfo=timezone.utc) if follow_up.due_at.tzinfo is None else follow_up.due_at
        events.append({
            "id": f"follow-up-{follow_up.id}",
            "title": f"Follow up with {contact.first_name} {contact.last_name}" if contact else "Outreach follow-up",
            "start": due_at.isoformat(),
            "end": (due_at + timedelta(minutes=30)).isoformat(),
            "all_day": False,
            "contact_id": contact.id if contact else None,
            "outreach_id": outreach.id if outreach else None,
            "status": follow_up.status,
        })
    return events


@app.post("/ai/query")
async def ai_query(payload: QueryRequest, request: Request, db: DbSession):
    user = current_user(request, db)
    outreach = db.scalars(select(Outreach).where(Outreach.user_id == user.id)).all()
    contacts = db.scalars(
        select(Contact).where(
            or_(Contact.owner_user_id == user.id, Contact.owner_user_id.is_(None))
        )
    ).all()
    latest_resume = db.scalar(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.user_id == user.id)
        .order_by(ResumeAnalysis.id.desc())
    )
    total = len(outreach)
    replied = sum(row.status == "replied" for row in outreach)
    awaiting = sum(row.status == "sent" for row in outreach)
    drafts = sum(row.status == "drafted" for row in outreach)
    response_rate = round(replied / total * 100, 1) if total else 0
    question = payload.question.lower()
    web_context = await current_web_context(payload.question)
    resume_context = ""
    if latest_resume:
        resume_context = (
            f"Resume filename: {latest_resume.filename}. "
            f"Extracted skills: {latest_resume.skills or 'none'}. "
            f"Resume text excerpt: {latest_resume.extracted_text[:6000]}"
        )
    if "capital of india" in question:
        answer = "The capital of India is New Delhi."
    elif any(word in question for word in ("hello", "hi ", "hey", "thanks", "thank you")):
        answer = "Hi! I can help with outreach planning, contacts, follow-ups, message ideas, response rates, and resume matching."
    elif "response" in question or "repl" in question:
        answer = f"You have received {replied} responses from {total} outreach messages, a {response_rate}% response rate."
    elif "appl" in question or "compan" in question or "employer" in question:
        answer = f"You have contacted {total} companies. Use Resume profile to find matching employers, or Contacts to search companies and connect with people."
    elif "contact" in question:
        names = ", ".join(f"{contact.first_name} {contact.last_name}".strip() for contact in contacts[:5])
        answer = f"You have {len(contacts)} contacts in your workspace."
        if names:
            answer += f" Some contacts are {names}."
    elif "await" in question or "follow" in question or "pending" in question:
        answer = f"You currently have {awaiting} outreach messages awaiting a reply and {drafts} drafts. Review the oldest pending conversation next."
    elif any(word in question for word in ("draft", "write", "email", "message", "subject", "introduce")):
        answer = "For a personalized message, open Contacts, choose a person, and use the draft assistant. Review the subject and message before sending."
    elif any(word in question for word in ("resume", "skill", "career", "role", "job")):
        if "machine learning" in question or "ml intern" in question or "intern" in question:
            answer = (
                "For a strong machine learning intern resume, focus on: "
                "1) a concise skills section covering Python, NumPy, pandas, scikit-learn, PyTorch or TensorFlow, SQL, and Git; "
                "2) two or three projects with the problem, dataset, method, evaluation metric, and measurable result; "
                "3) quantified impact such as accuracy, F1 score, latency, dataset size, or training-time improvements; "
                "4) internship, research, coursework, hackathon, or open-source experience; "
                "5) links to a clean GitHub portfolio and project demos; and "
                "6) a one-page layout tailored to the job description. "
                "Lead each bullet with a strong verb and explain what you built, how you built it, and why it mattered."
            )
        elif latest_resume:
            skills = latest_resume.skills or "no skills extracted yet"
            answer = f"Your latest resume analysis found: {skills}. Use Resume profile to review the match score and see employers ranked by fit."
        else:
            answer = "Upload your resume in Resume profile first. I can then help interpret your skills and employer matches."
    elif any(word in question for word in ("today", "daily", "sent", "limit")):
        answer = f"You have sent {sum(row.status == 'sent' for row in outreach)} messages in the current workspace view. Your daily sending limit is configured in Settings."
    elif web_context:
        answer = web_context
    else:
        answer = "I can answer general knowledge questions, career questions, and questions about your workspace. Please try asking that another way."
    if settings.groq_api_key:
        try:
            prompt = ChatPromptTemplate.from_messages([
                (
                    "system",
                    "You are Follow Up Fox, a helpful general-purpose, resume, career, and outreach assistant. Preserve your broad general knowledge: answer basic questions directly, including geography, history, science, technology, everyday topics, and career questions. Use the user's resume and workspace data when relevant. Clearly distinguish facts from the workspace, general knowledge, and web context. Do not invent current facts; if web context is incomplete, say so.",
                ),
                MessagesPlaceholder("history", optional=True),
                (
                    "human",
                    "Workspace metrics: total={total}, replied={replied}, awaiting={awaiting}, drafts={drafts}. "
                    "Contacts: {contacts}. {resume_context} {web_context} Question: {question}",
                ),
            ])
            llm = ChatGroq(
                model=settings.groq_model,
                temperature=0.3,
                api_key=settings.groq_api_key,
                timeout=20,
            )
            history = [
                (
                    "human" if item["role"] == "user" else "ai",
                    item["content"][:1000],
                )
                for item in payload.history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            ]
            response = await (prompt | llm).ainvoke({
                "history": history,
                "total": total,
                "replied": replied,
                "awaiting": awaiting,
                "drafts": drafts,
                "contacts": ", ".join(contact.company for contact in contacts[:30]) or "none",
                "resume_context": resume_context or "No resume has been uploaded.",
                "web_context": web_context or "No web result was available; use general knowledge for non-current questions.",
                "question": payload.question,
            })
            answer = message_text(response).strip()
        except (GroqError, KeyError, TypeError, ValueError):
            pass
    return {
        "answer": answer,
        "metrics": {
            "total": total,
            "replied": replied,
            "awaiting": awaiting,
            "drafts": drafts,
            "response_rate": response_rate,
        },
    }
