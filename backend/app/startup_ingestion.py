from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .main import SessionLocal, StartupJob, StartupLead


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_record(record: dict) -> list[str]:
    required = ("company", "official_domain", "website", "source_url", "source_type")
    missing = [field for field in required if not str(record.get(field, "")).strip()]
    errors = list(missing)
    confidence = record.get("confidence", 0)
    if not isinstance(confidence, int) or not 0 <= confidence <= 100:
        errors.append("confidence must be an integer from 0 to 100")
    email = str(record.get("contact_email") or "")
    if email and "@" not in email:
        errors.append("contact_email is not a valid email")
    linkedin_url = str(record.get("linkedin_url") or "").strip()
    if not email and not linkedin_url:
        errors.append("contact_email or linkedin_url is required")
    return errors


def ingest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else payload.get("companies", [])
    inserted = updated = jobs_inserted = skipped = 0
    skipped_reasons: dict[str, int] = {}
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        for record in records:
            errors = validate_record(record)
            if errors:
                skipped += 1
                for error in errors:
                    skipped_reasons[error] = skipped_reasons.get(error, 0) + 1
                continue
            domain = str(record["official_domain"]).strip().lower()
            startup = db.scalar(select(StartupLead).where(StartupLead.official_domain == domain))
            if startup is None:
                startup = StartupLead(official_domain=domain)
                db.add(startup)
                inserted += 1
            else:
                updated += 1

            for field in (
                "company", "website", "industry", "location", "stage",
                "contact_email", "linkedin_url", "contact_category", "source_url", "source_type",
                "confidence", "review_status", "notes",
            ):
                if field in record and record[field] is not None:
                    setattr(startup, field, record[field])
            startup.last_verified_at = now
            db.flush()

            for job_data in record.get("jobs", []):
                posting_url = str(job_data.get("posting_url", "")).strip()
                title = str(job_data.get("title", "")).strip()
                if not posting_url or not title:
                    continue
                job = db.scalar(select(StartupJob).where(StartupJob.posting_url == posting_url))
                if job is None:
                    job = StartupJob(startup_id=startup.id, posting_url=posting_url, title=title)
                    db.add(job)
                    jobs_inserted += 1
                for field in ("title", "department", "location", "employment_type", "source_type", "status"):
                    if field in job_data and job_data[field] is not None:
                        setattr(job, field, job_data[field])
                if job_data.get("posted_at"):
                    job.posted_at = parse_datetime(job_data["posted_at"])
                job.last_seen_at = now
        db.commit()
    result = {"inserted": inserted, "updated": updated, "jobs_inserted": jobs_inserted, "skipped": skipped}
    if skipped_reasons:
        result["skipped_reasons"] = skipped_reasons
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest sourced startup leads and jobs into Follow Up Fox.")
    parser.add_argument("input", type=Path, help="JSON file containing startup records")
    args = parser.parse_args()
    print(json.dumps(ingest(args.input), indent=2))
