import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import main as main_module
from backend.app.main import (
    Base,
    Company,
    Contact,
    ContactCreate,
    Job,
    MAX_RESUME_BYTES,
    Outreach,
    ResumeAnalysis,
    SettingsUpdate,
    User,
    attach_resume_file,
    app,
    calculate_match_scores,
    db_session,
    employer_payload,
    fernet,
    signer,
    settings,
)
from backend.app.resume_matching import fallback_skills, infer_job_skills, parse_skill_response
from backend.app.firebase_store import (
    FIREBASE_DATABASE_SCOPES,
    FirebaseRealtimeStore,
    _from_firebase,
    create_firebase_session,
)
from db import import_hiring_csv


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_firebase_store_builds_valid_root_and_nested_urls():
    store = FirebaseRealtimeStore(
        "https://example-default-rtdb.firebaseio.com/"
    )

    assert store._url() == "https://example-default-rtdb.firebaseio.com/.json"
    assert store._url("_meta/counters/users") == (
        "https://example-default-rtdb.firebaseio.com/_meta/counters/users.json"
    )


def test_firebase_store_requests_required_google_auth_scopes():
    assert FIREBASE_DATABASE_SCOPES == [
        "https://www.googleapis.com/auth/firebase.database",
        "https://www.googleapis.com/auth/userinfo.email",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (base64.b64encode(b"resume bytes").decode("ascii"), b"resume bytes"),
        (repr(b"legacy resume bytes"), b"legacy resume bytes"),
    ],
)
def test_firebase_store_reads_base64_and_legacy_bytes_values(value, expected):
    column = ResumeAnalysis.__table__.c.file_content

    assert _from_firebase(column, value) == expected


def test_firebase_session_skips_duplicate_job_source_and_title():
    class TestBase(DeclarativeBase):
        pass

    class TestJob(TestBase):
        __tablename__ = "jobs"
        __table_args__ = (UniqueConstraint("source_url", "title"),)

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        source_url: Mapped[str] = mapped_column(String)
        title: Mapped[str] = mapped_column(String)

    class FakeFirebaseStore:
        def read_root(self):
            return {
                "jobs": [
                    {"id": 20, "source_url": "https://example.com/job", "title": "Engineer"},
                    {"id": 194, "source_url": "https://example.com/job", "title": "Engineer"},
                ]
            }

    session = create_firebase_session(TestBase, FakeFirebaseStore())
    try:
        assert session.scalars(select(TestJob)).all() == [
            session.get(TestJob, 20)
        ]
    finally:
        session.close()


def test_firebase_session_skips_rows_with_orphaned_foreign_keys():
    class TestBase(DeclarativeBase):
        pass

    class TestContact(TestBase):
        __tablename__ = "contacts"

        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    class TestJob(TestBase):
        __tablename__ = "jobs"

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id"))

    class FakeFirebaseStore:
        def read_root(self):
            return {
                "contacts": [{"id": 1}],
                "jobs": [
                    {"id": 10, "contact_id": 1},
                    {"id": 11, "contact_id": 2},
                ],
            }

    session = create_firebase_session(TestBase, FakeFirebaseStore())
    try:
        assert [job.id for job in session.scalars(select(TestJob)).all()] == [10]
    finally:
        session.close()


def test_firebase_session_uses_last_record_for_duplicate_primary_key():
    class TestBase(DeclarativeBase):
        pass

    class TestUser(TestBase):
        __tablename__ = "users"

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        email: Mapped[str] = mapped_column(String)

    class FakeFirebaseStore:
        def read_root(self):
            return {
                "users": [
                    {"id": 1, "email": "older@example.com"},
                    {"id": 1, "email": "current@example.com"},
                ]
            }

    session = create_firebase_session(TestBase, FakeFirebaseStore())
    try:
        user = session.get(TestUser, 1)
        assert user is not None
        assert user.email == "current@example.com"
    finally:
        session.close()


@pytest.mark.parametrize("path", ["/settings", "/outreach", "/calendar/events?start=2026-11-01T00:00:00Z&end=2026-12-01T00:00:00Z", "/ai/query"])
def test_protected_routes_require_authentication(path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db_session():
        with test_sessions() as session:
            yield session

    app.dependency_overrides[db_session] = override_db_session
    try:
        response = (
            client.get(path)
            if path != "/ai/query"
            else client.post(path, json={"question": "hello"})
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_google_calendar_and_tasks_routes_are_removed():
    assert client.get("/tasks").status_code == 404
    assert client.post("/follow-ups", json={}).status_code == 404


def test_google_oauth_no_longer_requests_calendar_or_tasks_scopes():
    assert "https://www.googleapis.com/auth/gmail.modify" in main_module.SCOPES
    assert not any("calendar" in scope or "tasks" in scope for scope in main_module.SCOPES)


def test_firebase_session_reads_and_persists_table_rows_without_a_file_database():
    class FakeFirebaseStore:
        def __init__(self):
            self.root = {
                "users": {
                    "7": {
                        "id": 7,
                        "email": "firebase-user@example.com",
                        "full_name": "Firebase User",
                        "user_type": "fresher",
                        "plan": "free",
                        "daily_draft_limit": 10,
                        "daily_send_limit": 20,
                        "gmail_connected": False,
                        "professional_headline": "",
                        "about": "",
                        "human_approval_required": True,
                        "followup_delay_days": 5,
                    }
                }
            }
            self.updates = []

        def read_root(self):
            return self.root

        def allocate_id(self, table_name, minimum):
            return minimum + 1

        def update_paths(self, updates):
            self.updates.append(updates)
            for path, value in updates.items():
                table_name, row_key = path.split("/", 1)
                self.root.setdefault(table_name, {})
                if value is None:
                    self.root[table_name].pop(row_key, None)
                else:
                    self.root[table_name][row_key] = value

    firebase = FakeFirebaseStore()
    session = create_firebase_session(Base, firebase)
    try:
        existing = session.get(User, 7)
        assert existing is not None
        assert existing.email == "firebase-user@example.com"

        existing.about = "Updated profile"
        session.commit()
        assert firebase.root["users"]["7"]["about"] == "Updated profile"

        created = User(email="new-firebase-user@example.com", name="New Firebase User")
        session.add(created)
        session.commit()
        assert created.id == 8
        assert firebase.root["users"]["8"]["email"] == "new-firebase-user@example.com"
        assert firebase.root["users"]["8"]["full_name"] == "New Firebase User"

        session.delete(created)
        session.commit()
        assert "8" not in firebase.root["users"]
    finally:
        session.close()
        session.get_bind().dispose()


def test_firebase_session_persists_contact_owner_and_category_changes():
    class FakeFirebaseStore:
        def __init__(self):
            self.root = {
                "users": {
                    "7": {
                        "id": 7,
                        "email": "connect-test@example.com",
                        "full_name": "Connect Test",
                        "user_type": "fresher",
                        "plan": "free",
                        "daily_draft_limit": 10,
                        "daily_send_limit": 20,
                        "gmail_connected": False,
                        "professional_headline": "",
                        "about": "",
                        "human_approval_required": True,
                        "followup_delay_days": 5,
                    }
                },
                "companies": {
                    "1": {
                        "id": 1,
                        "name": "Connect Test Company",
                        "domain": "connect-test.invalid",
                        "industry": "Technology",
                        "size_band": "small",
                        "hq_city": "Toronto",
                    }
                },
                "contacts": {
                    "2": {
                        "id": 2,
                        "company_id": 1,
                        "owner_user_id": None,
                        "full_name": "Hiring Team",
                        "first_name": "Hiring",
                        "last_name": "Team",
                        "job_title": "Recruiter",
                        "role": "Recruiter",
                        "company": "Connect Test Company",
                        "email": "recruiter@connect-test.example",
                        "linkedin_url": None,
                        "notes": "",
                        "category": "employer",
                        "source": "test",
                        "bounce_count": 0,
                        "opted_out": False,
                    }
                },
            }
            self.updates = []

        def read_root(self):
            return self.root

        def allocate_id(self, table_name, minimum):
            return minimum + 1

        def update_paths(self, updates):
            self.updates.append(updates)
            for path, value in updates.items():
                table_name, row_key = path.split("/", 1)
                self.root.setdefault(table_name, {})
                if value is None:
                    self.root[table_name].pop(row_key, None)
                else:
                    self.root[table_name][row_key] = value

    firebase = FakeFirebaseStore()
    session = create_firebase_session(Base, firebase)
    try:
        contact = session.get(Contact, 2)
        assert contact is not None
        contact.owner_user_id = 7
        contact.category = "my"
        session.commit()
        assert firebase.root["contacts"]["2"]["owner_user_id"] == 7
        assert firebase.root["contacts"]["2"]["category"] == "my"
    finally:
        session.close()


def test_hiring_csv_import_persists_through_firebase(monkeypatch, tmp_path):
    class FakeFirebaseStore:
        def __init__(self):
            self.root = {}

        def read_root(self):
            return self.root

        def allocate_id(self, table_name, minimum):
            return minimum + 1

        def update_paths(self, updates):
            for path, value in updates.items():
                table_name, row_key = path.split("/", 1)
                self.root.setdefault(table_name, {})
                if value is None:
                    self.root[table_name].pop(row_key, None)
                else:
                    self.root[table_name][row_key] = value

    firebase = FakeFirebaseStore()
    csv_path = tmp_path / "hiring.csv"
    csv_path.write_text(
        "company,official_domain,industry,location,hiring_roles,job_url,contact_email,"
        "linkedin_url,source_url,source_type,confidence,review_status,notes\n"
        "Example,example.com,Technology,Remote,Software Engineer,https://example.com/apply,"
        "hiring@example.com,,https://example.com/careers,official_website,90,approved,Python role\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        import_hiring_csv,
        "SessionLocal",
        lambda: create_firebase_session(Base, firebase),
    )
    assert import_hiring_csv.import_csv(csv_path) == (1, 1, 1)
    assert firebase.root["companies"]["1"]["domain"] == "example.com"
    assert firebase.root["contacts"]["1"]["email"] == "hiring@example.com"
    assert firebase.root["jobs"]["1"]["source_url"] == "https://example.com/careers"


def test_internal_calendar_lists_only_local_follow_ups(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as session:
        user = User(email="internal-calendar-test@example.com", name="Calendar Test")
        company = Company(name="Calendar Co", domain="calendar.invalid", industry="Technology", hq_city="Toronto")
        session.add_all((user, company))
        session.flush()
        contact = Contact(
            company_id=company.id,
            owner_user_id=user.id,
            full_name="Taylor Recruiter",
            first_name="Taylor",
            last_name="Recruiter",
            job_title="Recruiter",
            role="Recruiter",
            company=company.name,
            email="taylor@calendar.invalid",
        )
        resume = ResumeAnalysis(user_id=user.id, filename="resume.txt", extracted_text="", skills="[]")
        session.add_all((contact, resume))
        session.flush()
        job = Job(company_id=company.id, contact_id=contact.id, title="Engineer", description="", location="Remote")
        session.add(job)
        session.flush()
        outreach = Outreach(
            user_id=user.id,
            resume_id=resume.id,
            contact_id=contact.id,
            job_id=job.id,
            subject="Hello",
            body="Checking in",
            status="sent",
        )
        session.add(outreach)
        session.flush()
        session.add(main_module.FollowUp(
            outreach_id=outreach.id,
            due_at=datetime(2026, 11, 12, 14, 30, tzinfo=timezone.utc),
        ))
        session.commit()
        user_id = user.id

    def override_db_session():
        with test_sessions() as session:
            yield session

    def unexpected_google_credentials(_user):
        pytest.fail("The internal calendar must not access Google Calendar or Tasks.")

    monkeypatch.setattr(main_module, "google_credentials", unexpected_google_credentials)
    app.dependency_overrides[db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            response = test_client.get(
                "/calendar/events?start=2026-11-01T00:00:00Z&end=2026-12-01T00:00:00Z",
                cookies=cookie,
            )
            assert response.status_code == 200
            assert response.json()[0]["title"] == "Follow up with Taylor Recruiter"
            assert response.json()[0]["contact_id"] is not None
            assert response.json()[0]["outreach_id"] is not None
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_follow_up_draft_reuses_existing_outreach(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as session:
        user = User(email="follow-up-draft@example.com", name="Follow-up Test")
        company = Company(
            name="Follow-up Co",
            domain="follow-up.invalid",
            industry="Technology",
            hq_city="Toronto",
        )
        session.add_all((user, company))
        session.flush()
        contact = Contact(
            company_id=company.id,
            owner_user_id=user.id,
            full_name="Taylor Recruiter",
            first_name="Taylor",
            last_name="Recruiter",
            job_title="Recruiter",
            role="Recruiter",
            company=company.name,
            email="taylor@follow-up.example",
        )
        resume = ResumeAnalysis(
            user_id=user.id,
            filename="resume.txt",
            extracted_text="",
            skills="[]",
        )
        session.add_all((contact, resume))
        session.flush()
        job = Job(
            company_id=company.id,
            contact_id=contact.id,
            title="Engineer",
            description="",
            location="Remote",
        )
        session.add(job)
        session.flush()
        outreach = Outreach(
            user_id=user.id,
            resume_id=resume.id,
            contact_id=contact.id,
            job_id=job.id,
            subject="Hello",
            body="My original message",
            status="sent",
        )
        session.add(outreach)
        session.commit()
        user_id, contact_id, outreach_id = user.id, contact.id, outreach.id

    def override_db_session():
        with test_sessions() as session:
            yield session

    monkeypatch.setattr(settings, "groq_api_key", "")
    app.dependency_overrides[db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            response = test_client.post(
                "/drafts/suggest",
                json={
                    "contact_id": contact_id,
                    "follow_up": True,
                    "outreach_id": outreach_id,
                },
                cookies=cookie,
            )
            assert response.status_code == 200
            assert response.json()["follow_up"] is True
            assert response.json()["outreach_id"] == outreach_id
            assert response.json()["subject"] == "Re: Hello"
            assert "follow up" in response.json()["body"].lower()
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_settings_update_rejects_unsafe_limits():
    with pytest.raises(ValidationError):
        SettingsUpdate(daily_send_limit=0)


def test_settings_update_accepts_supported_values():
    values = SettingsUpdate(human_approval_required=False, followup_delay_days=7, daily_send_limit=30)
    assert values.model_dump() == {
        "human_approval_required": False,
        "followup_delay_days": 7,
        "daily_send_limit": 30,
    }


def test_contact_requires_email_or_linkedin():
    with pytest.raises(ValidationError):
        ContactCreate(first_name="Hiring", last_name="Team", company="Example", role="Hiring contact")


def test_contact_accepts_linkedin_without_email():
    contact = ContactCreate(
        first_name="Hiring",
        last_name="Team",
        company="Example",
        role="Hiring contact",
        linkedin_url="https://www.linkedin.com/company/example",
    )
    assert contact.email is None


def test_creating_my_contact_persists_to_sql_and_lists_it():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as session:
        user = User(email="my-contact-test@example.com", name="My Contact Test")
        session.add(user)
        session.commit()
        user_id = user.id

    def override_db_session():
        with test_sessions() as session:
            yield session

    app.dependency_overrides[db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            created = test_client.post(
                "/contacts",
                json={
                    "first_name": "Taylor",
                    "last_name": "Recruiter",
                    "email": "taylor@my-contact-test.example",
                    "company": "My Contact Test Company",
                    "role": "Recruiter",
                },
                cookies=cookie,
            )
            assert created.status_code == 201
            contact_id = created.json()["id"]

            with test_sessions() as session:
                saved_contact = session.get(Contact, contact_id)
                assert saved_contact is not None
                assert saved_contact.owner_user_id == user_id
                assert saved_contact.category == "my"

            listed = test_client.get("/contacts?category=my", cookies=cookie)
            assert listed.status_code == 200
            assert any(row["id"] == contact_id for row in listed.json())
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_employer_payload_includes_company_location_and_hiring_roles():
    employers = employer_payload([
        {
            "job_id": 1,
            "contact_id": 11,
            "company_name": "Example Inc.",
            "company_location": "Toronto",
            "position": "Software Engineer",
            "hr_name": "Hiring Team",
            "hr_email": "hr@example.com",
            "skill_match_percentage": 80,
        },
        {
            "job_id": 2,
            "contact_id": 11,
            "company_name": "Example Inc.",
            "company_location": "Toronto",
            "position": "Data Analyst",
            "hr_name": "Hiring Team",
            "hr_email": "hr@example.com",
            "skill_match_percentage": 60,
        },
    ])

    assert employers[0]["location"] == "Toronto"
    assert employers[0]["hiring_roles"] == ["Software Engineer", "Data Analyst"]
    assert employers[0]["contact_eligible"] is True
    assert employers[0]["email_eligible"] is True


def test_employer_without_email_includes_linkedin_contact_method():
    employer = employer_payload([
        {
            "job_id": 1,
            "contact_id": 12,
            "company_name": "LinkedIn Only Inc.",
            "company_location": "Toronto",
            "position": "Engineer",
            "hr_name": "Hiring Team",
            "hr_email": "unknown.invalid",
            "contact_linkedin": "https://www.linkedin.com/in/hiring-team",
            "skill_match_percentage": 50,
        },
    ])[0]

    assert employer["contact_email"] is None
    assert employer["email_eligible"] is False
    assert employer["contact_eligible"] is True
    assert employer["contact_linkedin"] == "https://www.linkedin.com/in/hiring-team"


def test_supplied_employer_email_is_available_for_email_outreach():
    employer = employer_payload([
        {
            "job_id": 1,
            "contact_id": 12,
            "company_name": "Supplied Email Inc.",
            "company_location": "Toronto",
            "position": "Engineer",
            "hr_name": "Hiring Team",
            "hr_email": "hiring@example.com",
            "skill_match_percentage": 50,
        },
    ])[0]

    assert employer["contact_eligible"] is True
    assert employer["email_eligible"] is True
    assert employer["contact_email"] == "hiring@example.com"


def test_connecting_employer_contact_preserves_supplied_email():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as session:
        user = User(email="connect-test@example.com", name="Connect Test")
        other_user = User(email="other-connect-test@example.com", name="Other User")
        company = Company(
            name="Connect Test Company",
            domain="connect-test.invalid",
            industry="Technology",
            size_band="small",
            hq_city="Toronto",
        )
        session.add_all((user, other_user, company))
        session.flush()
        contact = Contact(
            company_id=company.id,
            full_name="Hiring Team",
            first_name="Hiring",
            last_name="Team",
            job_title="Recruiter",
            role="Recruiter",
            company=company.name,
            email="recruiter@connect-test.example",
            source="test",
            category="employer",
        )
        session.add(contact)
        session.commit()
        user_id, other_user_id, contact_id = user.id, other_user.id, contact.id

    def override_db_session():
        with test_sessions() as session:
            yield session

    app.dependency_overrides[db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            response = test_client.post(f"/contacts/{contact_id}/connect", cookies=cookie)
            assert response.status_code == 200
            assert response.json() == {"id": contact_id, "connected": True}

            contact_response = test_client.get("/contacts?category=my", cookies=cookie)
            assert contact_response.status_code == 200
            connected_contact = next(
                row for row in contact_response.json() if row["id"] == contact_id
            )
            assert connected_contact["category"] == "my"
            assert connected_contact["connected"] is True
            assert connected_contact["email"] == "recruiter@connect-test.example"
            assert connected_contact["email_eligible"] is True
            assert connected_contact["email_eligibility_reason"] is None
            employer_contacts = test_client.get(
                "/contacts?category=employer", cookies=cookie
            )
            assert all(row["id"] != contact_id for row in employer_contacts.json())

            repeated = test_client.post(f"/contacts/{contact_id}/connect", cookies=cookie)
            assert repeated.status_code == 200

            other_cookie = {"outreach_session": signer.dumps({"user_id": other_user_id})}
            conflict = test_client.post(
                f"/contacts/{contact_id}/connect",
                cookies=other_cookie,
            )
            assert conflict.status_code == 409
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_regenerating_draft_returns_a_different_fallback(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as session:
        user = User(email="draft-test@example.com", name="Draft Test")
        company = Company(
            name="Draft Test Company",
            domain="draft-test.invalid",
            industry="Technology",
            size_band="small",
            hq_city="Toronto",
        )
        session.add_all((user, company))
        session.flush()
        contact = Contact(
            company_id=company.id,
            full_name="Hiring Team",
            first_name="Hiring",
            last_name="Team",
            job_title="Recruiter",
            role="Recruiter",
            company=company.name,
            email="recruiter@draft-test.example",
            source="test",
            category="employer",
        )
        session.add(contact)
        session.commit()
        user_id, contact_id = user.id, contact.id

    def override_db_session():
        with test_sessions() as session:
            yield session

    monkeypatch.setattr(settings, "groq_api_key", "")
    app.dependency_overrides[db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            first = test_client.post(
                "/drafts/suggest",
                json={"contact_id": contact_id},
                cookies=cookie,
            )
            assert first.status_code == 200
            first_draft = first.json()

            regenerated = test_client.post(
                "/drafts/suggest",
                json={
                    "contact_id": contact_id,
                    "previous_subject": first_draft["subject"],
                    "previous_body": first_draft["body"],
                },
                cookies=cookie,
            )
            assert regenerated.status_code == 200
            second_draft = regenerated.json()
            assert (second_draft["subject"], second_draft["body"]) != (
                first_draft["subject"],
                first_draft["body"],
            )
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_resume_upload_stores_encrypted_original_for_attachment(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as session:
        user = User(email="resume-file-test@example.com", name="Resume File Test")
        other_user = User(email="other-resume-file-test@example.com", name="Other Resume Test")
        session.add_all((user, other_user))
        session.commit()
        user_id, other_user_id = user.id, other_user.id

    def override_db_session():
        with test_sessions() as session:
            yield session

    async def fake_ai_skills(_text):
        return ["python"]

    monkeypatch.setattr(main_module, "ai_skills", fake_ai_skills)
    app.dependency_overrides[db_session] = override_db_session
    content = b"Resume with Python and project experience."
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            response = test_client.post(
                "/resume/analyze",
                files={"file": ("my-resume.txt", content, "text/plain")},
                cookies=cookie,
            )
            assert response.status_code == 200
            assert response.json()["resume_available"] is True
            assert response.json()["resume_filename"] == "my-resume.txt"
            preview = test_client.get("/resume/latest/file", cookies=cookie)
            assert preview.status_code == 200
            assert preview.content == content
            assert preview.headers["content-type"].startswith("text/plain")
            other_cookie = {"outreach_session": signer.dumps({"user_id": other_user_id})}
            unauthorized_preview = test_client.get(
                "/resume/latest/file",
                cookies=other_cookie,
            )
            assert unauthorized_preview.status_code == 404

            with test_sessions() as session:
                resume = session.scalar(
                    select(ResumeAnalysis).where(ResumeAnalysis.user_id == user_id)
                )
                assert resume is not None
                assert resume.file_content != content
                assert fernet.decrypt(resume.file_content) == content
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_resume_attachment_is_included_in_email_message():
    content = b"%PDF-1.7 example resume"
    resume = ResumeAnalysis(
        user_id=5,
        filename="candidate-resume.pdf",
        file_content=fernet.encrypt(content),
    )
    message = EmailMessage()
    message.set_content("Hello")

    attach_resume_file(message, resume, user_id=5)

    attachment, = list(message.iter_attachments())
    assert attachment.get_filename() == "candidate-resume.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True) == content


def test_resume_attachment_rejects_another_users_resume():
    message = EmailMessage()
    resume = ResumeAnalysis(user_id=8, filename="resume.pdf", file_content=fernet.encrypt(b"resume"))

    with pytest.raises(HTTPException) as error:
        attach_resume_file(message, resume, user_id=5)

    assert error.value.status_code == 404


@pytest.mark.parametrize(
    ("attach_resume", "follow_up_mode"),
    [(False, False), (True, False), (False, True)],
)
def test_send_outreach_includes_resume_only_when_selected(monkeypatch, attach_resume, follow_up_mode):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    resume_content = b"%PDF-1.7 reviewed resume content"
    with test_sessions() as session:
        user = User(
            email="send-attachment-test@example.com",
            name="Attachment Test",
            refresh_token="token",
            followup_delay_days=3,
        )
        company = Company(
            name="Attachment Test Company",
            domain="attachment-test.invalid",
            industry="Technology",
            size_band="small",
            hq_city="Toronto",
        )
        session.add_all((user, company))
        session.flush()
        contact = Contact(
            company_id=company.id,
            full_name="Hiring Team",
            first_name="Hiring",
            last_name="Team",
            job_title="Recruiter",
            role="Recruiter",
            company=company.name,
            email="recruiter@attachment-test.example",
            source="test",
            category="employer",
        )
        resume = ResumeAnalysis(
            user_id=user.id,
            filename="candidate.pdf",
            extracted_text="Resume",
            file_content=fernet.encrypt(resume_content),
            is_active=True,
        )
        session.add_all((contact, resume))
        session.flush()
        job = Job(
            company_id=company.id,
            contact_id=contact.id,
            title="Engineer",
            description="",
            location="Remote",
            work_mode="remote",
            experience_level="junior",
        )
        session.add(job)
        session.flush()
        outreach = Outreach(
            user_id=user.id,
            resume_id=resume.id,
            contact_id=contact.id,
            job_id=job.id,
            subject="Hello",
            body="Message body",
            status="sent" if follow_up_mode else "drafted",
            gmail_draft_id=None if follow_up_mode else "gmail-draft-id",
            thread_id="gmail-thread-id" if follow_up_mode else None,
        )
        session.add(outreach)
        session.commit()
        user_id, outreach_id = user.id, outreach.id

    class FakeRequest:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class FakeDrafts:
        def update(self, **kwargs):
            self.body = kwargs["body"]
            return FakeRequest({"id": "gmail-draft-id"})

        def send(self, **_kwargs):
            return FakeRequest({"id": "gmail-message-id", "threadId": "gmail-thread-id"})

    class FakeMessages:
        def send(self, **kwargs):
            self.body = kwargs["body"]
            return FakeRequest({"id": "gmail-follow-up-id", "threadId": "gmail-thread-id"})

    class FakeGmail:
        def __init__(self):
            self.draft_service = FakeDrafts()
            self.message_service = FakeMessages()

        def users(self):
            return self

        def drafts(self):
            return self.draft_service

        def messages(self):
            return self.message_service

    def override_db_session():
        with test_sessions() as session:
            yield session

    fake_gmail = FakeGmail()
    monkeypatch.setattr(main_module, "google_credentials", lambda _user: object())
    monkeypatch.setattr(main_module, "build", lambda *_args, **_kwargs: fake_gmail)
    app.dependency_overrides[db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            cookie = {"outreach_session": signer.dumps({"user_id": user_id})}
            response = test_client.post(
                "/outreach/send",
                json={
                    "outreach_id": outreach_id,
                    "subject": "Hello",
                    "body": "Message body",
                    "attach_resume": attach_resume,
                    "follow_up": follow_up_mode,
                },
                cookies=cookie,
            )
            assert response.status_code == 200
            assert response.json()["follow_up_due"]
            with test_sessions() as session:
                saved_outreach = session.get(Outreach, outreach_id)
                follow_up = session.scalar(
                    select(main_module.FollowUp).where(
                        main_module.FollowUp.outreach_id == outreach_id
                    )
                )
                assert saved_outreach is not None
                assert follow_up is not None
                assert follow_up.due_at - saved_outreach.sent_at == timedelta(days=3)
            raw_message = (
                fake_gmail.message_service.body["raw"]
                if follow_up_mode
                else fake_gmail.draft_service.body["message"]["raw"]
            )
            decoded = base64.urlsafe_b64decode(raw_message + "=" * (-len(raw_message) % 4))
            parsed_message = BytesParser(policy=policy.default).parsebytes(decoded)
            attachments = list(parsed_message.iter_attachments())
            assert len(attachments) == int(attach_resume)
            if attach_resume:
                assert attachments[0].get_filename() == "candidate.pdf"
                assert attachments[0].get_payload(decode=True) == resume_content
    finally:
        app.dependency_overrides.pop(db_session, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_resume_upload_size_is_limited():
    assert MAX_RESUME_BYTES == 10 * 1024 * 1024


def test_resume_skill_aliases_normalize_nodejs():
    assert fallback_skills("React and NodeJS") == ["node.js", "react"]
    assert parse_skill_response('{"skills":["React.js","NodeJS"]}') == ["node.js", "react"]


def test_web_development_jobs_receive_a_matchable_skill():
    assert infer_job_skills("Web Development Intern", "") == {"web development"}
    assert infer_job_skills("Software Engineer Intern", "") == set()


def test_react_and_nodejs_resume_matches_web_development_job():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE resumes (id INTEGER PRIMARY KEY, user_id INTEGER);
        CREATE TABLE skills (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE resume_skills (resume_id INTEGER, skill_id INTEGER);
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, hq_city TEXT);
        CREATE TABLE contacts (
            id INTEGER PRIMARY KEY, full_name TEXT, email TEXT,
            linkedin_url TEXT, company_id INTEGER, opted_out INTEGER
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY, company_id INTEGER, contact_id INTEGER,
            title TEXT, experience_level TEXT, location TEXT,
            is_active INTEGER, posted_at TEXT
        );
        CREATE TABLE job_skills (job_id INTEGER, skill_id INTEGER);
        CREATE TABLE suppressions (email TEXT);
        CREATE TABLE outreach (user_id INTEGER, contact_id INTEGER);
        INSERT INTO resumes VALUES (1, 7);
        INSERT INTO skills VALUES (1, 'Node.js'), (2, 'React'), (3, 'Web Development');
        INSERT INTO resume_skills VALUES (1, 1), (1, 2);
        INSERT INTO companies VALUES (1, 'Example', 'Toronto');
        INSERT INTO contacts VALUES (1, 'Recruiter', 'hr@example.com', NULL, 1, 0);
        INSERT INTO contacts VALUES (
            2, 'LinkedIn Recruiter', NULL,
            'https://www.linkedin.com/in/recruiter', 1, 0
        );
        INSERT INTO jobs VALUES (
            1, 1, 1, 'Web Development Intern', 'intern', 'Remote', 1, '2026-11-07'
        );
        INSERT INTO jobs VALUES (
            2, 1, 2, 'Web Development Engineer', 'entry', 'Remote', 1, '2026-11-07'
        );
        INSERT INTO job_skills VALUES (1, 3), (2, 3);
        """
    )
    query = (Path(__file__).resolve().parents[2] / "db" / "match_jobs.sql").read_text(
        encoding="utf-8"
    )

    matches = connection.execute(query, {"resume_id": 1, "user_id": 7}).fetchall()

    assert len(matches) == 2
    match = matches[0]
    assert match[-1] == 100
    assert match[7] == "hr@example.com"
    assert matches[1][7] is None
    assert matches[1][8] == "https://www.linkedin.com/in/recruiter"

    connection.execute("UPDATE contacts SET email = 'anonymous@example.invalid' WHERE id = 1")
    invalid_matches = connection.execute(query, {"resume_id": 1, "user_id": 7}).fetchall()
    connection.close()
    assert match[3] == "Web Development Intern"
    assert all(row[4] != 1 for row in invalid_matches)
    assert [row[4] for row in invalid_matches] == [2]


def test_level_fit_scores_are_averages_of_actual_job_matches():
    overall, level_fit, counts = calculate_match_scores([
        {"experience_level": "intern", "skill_match_percentage": 100},
        {"experience_level": "junior", "skill_match_percentage": 0},
        {"experience_level": "fresher", "skill_match_percentage": 50},
        {"experience_level": "senior", "skill_match_percentage": 80},
    ])

    assert overall == 58
    assert level_fit == {"intern": 100, "entry": 25, "mid": None, "senior": 80}
    assert counts == {"intern": 1, "entry": 2, "mid": 0, "senior": 1}


def test_level_fit_without_jobs_is_not_fabricated():
    overall, level_fit, counts = calculate_match_scores([])

    assert overall == 0
    assert level_fit == {"intern": None, "entry": None, "mid": None, "senior": None}
    assert counts == {"intern": 0, "entry": 0, "mid": 0, "senior": 0}
