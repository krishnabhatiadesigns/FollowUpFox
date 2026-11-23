from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from backend.app.main import Company, Contact, Job, JobSkill, SessionLocal, Skill
from backend.app.resume_matching import infer_job_skills


REQUIRED_COLUMNS = {
    "company",
    "official_domain",
    "industry",
    "location",
    "hiring_roles",
    "job_url",
    "contact_email",
    "linkedin_url",
    "source_url",
    "source_type",
    "confidence",
    "review_status",
    "notes",
}


def experience_level(title: str) -> str:
    lowered = title.lower()
    if "intern" in lowered or "internship" in lowered:
        return "intern"
    if "fresher" in lowered or "entry-level" in lowered or "graduate" in lowered:
        return "fresher"
    if "senior" in lowered or "lead" in lowered:
        return "senior"
    if "mid" in lowered:
        return "mid"
    return "junior"


def import_csv(csv_path: Path) -> tuple[int, int, int]:
    with csv_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing_columns))}")
        rows = list(reader)

    company_count = contact_count = job_count = 0
    with SessionLocal() as db:
        for line_number, row in enumerate(rows, start=2):
            company_name = row["company"].strip()
            domain = row["official_domain"].strip().lower()
            if not company_name or not domain:
                raise ValueError(f"CSV line {line_number} must include company and official_domain")

            company = db.scalar(
                select(Company).where(func.lower(Company.domain) == domain)
            )
            if company is None:
                company = Company(
                    name=company_name,
                    domain=domain,
                    industry=row["industry"].strip() or "Unknown",
                    size_band="unknown",
                    hq_city=row["location"].strip() or "Unknown",
                )
                db.add(company)
                db.flush()
            else:
                company.name = company_name
                company.industry = row["industry"].strip() or "Unknown"
                company.hq_city = row["location"].strip() or "Unknown"
            company_count += 1

            email = row["contact_email"].strip().lower()
            if not email:
                digest = hashlib.sha256(f"{domain}\0missing".encode("utf-8")).hexdigest()[:16]
                email = f"anonymous+{digest}@example.invalid"
            category = row.get("contact_category", "").strip() or "unknown"
            notes = row["notes"].strip()
            contact_notes = (
                "Imported from hiring_employers_contacts.csv. "
                f"Contact category: {category}. "
                f"CSV review status: {row['review_status'].strip() or 'pending'}. "
                f"Confidence: {row['confidence'].strip() or '0'}. "
                f"Source: {row['source_url'].strip() or 'not supplied'}."
            )
            if notes:
                contact_notes = f"{contact_notes} {notes}"
            contact = db.scalar(
                select(Contact).where(func.lower(Contact.email) == email)
            )
            if contact is not None and contact.owner_user_id is not None:
                digest = hashlib.sha256(f"{domain}\0{email}".encode("utf-8")).hexdigest()[:16]
                email = f"anonymous+{digest}@example.invalid"
                contact = db.scalar(
                    select(Contact).where(func.lower(Contact.email) == email)
                )
            if contact is None:
                contact = Contact(
                    company_id=company.id,
                    owner_user_id=None,
                    full_name="Anonymous Hiring Contact",
                    first_name="Anonymous",
                    last_name="Hiring Contact",
                    job_title=category,
                    role=category,
                    company=company_name,
                    email=email,
                    linkedin_url=row["linkedin_url"].strip() or None,
                    notes=contact_notes,
                    category="employer",
                    source=row["source_type"].strip() or "csv_import",
                    opted_out=False,
                )
                db.add(contact)
                db.flush()
            elif contact.owner_user_id is None:
                contact.company_id = company.id
                contact.full_name = "Anonymous Hiring Contact"
                contact.first_name = "Anonymous"
                contact.last_name = "Hiring Contact"
                contact.job_title = category
                contact.role = category
                contact.company = company_name
                contact.linkedin_url = row["linkedin_url"].strip() or None
                contact.notes = contact_notes
                contact.source = row["source_type"].strip() or "csv_import"
            contact_count += 1

            location = row["location"].strip() or "Unknown"
            work_mode = (
                "remote"
                if "work from home" in location.lower() or "remote" in location.lower()
                else "onsite"
            )
            apply_by = row.get("apply_by", "").strip()
            is_active = not apply_by or apply_by >= date.today().isoformat()
            confidence = int(row["confidence"].strip() or 0)
            review_status = row["review_status"].strip().lower()
            if review_status not in {"pending", "approved", "rejected"}:
                review_status = "pending"
            roles = [role.strip() for role in row["hiring_roles"].split(";") if role.strip()]
            if not roles:
                roles = ["Position details unavailable"]
            job_url = row["job_url"].strip() or None
            source_url = row["source_url"].strip() or None

            for title in roles:
                job_query = select(Job).where(Job.title == title)
                if source_url:
                    job_query = job_query.where(Job.source_url == source_url)
                else:
                    job_query = job_query.where(
                        Job.company_id == company.id,
                        Job.apply_url == job_url,
                    )
                job = db.scalar(job_query.order_by(Job.id))
                if job is None:
                    job = Job(
                        company_id=company.id,
                        contact_id=contact.id,
                        title=title,
                        description=notes or title,
                        location=location,
                        apply_url=job_url,
                    )
                    db.add(job)
                    db.flush()
                job.company_id = company.id
                job.contact_id = contact.id
                job.description = notes or title
                job.location = location
                job.apply_url = job_url
                job.apply_by = apply_by or None
                job.source_url = source_url
                job.source_type = row["source_type"].strip() or "csv_import"
                job.review_status = review_status
                job.confidence = confidence
                job.notes = notes
                job.stage = row.get("stage", "").strip()
                job.work_mode = work_mode
                job.experience_level = experience_level(title)
                job.is_active = is_active

                existing_skills = db.scalars(
                    select(JobSkill).where(JobSkill.job_id == job.id)
                ).all()
                for existing_skill in existing_skills:
                    db.delete(existing_skill)
                for skill_name in infer_job_skills(title, notes):
                    skill = db.scalar(
                        select(Skill).where(func.lower(Skill.name) == skill_name.lower())
                    )
                    if skill is None:
                        skill = Skill(name=skill_name, category="job_match")
                        db.add(skill)
                        db.flush()
                    db.add(JobSkill(job_id=job.id, skill_id=skill.id, is_required=True))
                job_count += 1
        db.commit()
    return company_count, contact_count, job_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import company and job records from CSV into Firebase.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=Path.home() / "Downloads" / "hiring_employers_contacts.csv",
    )
    args = parser.parse_args()
    counts = import_csv(args.csv_path.resolve())
    print(json.dumps({
        "company_records": counts[0],
        "contact_records": counts[1],
        "job_records": counts[2],
        "destination": "Firebase Realtime Database",
    }))


if __name__ == "__main__":
    main()
