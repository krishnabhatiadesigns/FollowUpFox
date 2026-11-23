from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import or_, select

from .main import Contact, SessionLocal, StartupLead, User


def import_startup_contacts(user_id: int) -> dict[str, int]:
    inserted = skipped = 0
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise ValueError(f"user {user_id} not found")
        leads = db.scalars(select(StartupLead).order_by(StartupLead.id)).all()
        for lead in leads:
            email = lead.contact_email.strip() if lead.contact_email else None
            linkedin_url = lead.linkedin_url.strip() if lead.linkedin_url else None
            if not email and not linkedin_url:
                skipped += 1
                continue
            duplicate_conditions = []
            if email:
                duplicate_conditions.append(Contact.email == email)
            if linkedin_url:
                duplicate_conditions.append(Contact.linkedin_url == linkedin_url)
            duplicate = db.scalar(
                select(Contact).where(
                    Contact.user_id == user_id,
                    Contact.company == lead.company,
                    or_(*duplicate_conditions),
                )
            )
            if duplicate is not None:
                skipped += 1
                continue
            role = (
                f"{lead.contact_category.title()} contact"
                if lead.contact_category
                else "Hiring contact"
            )
            db.add(
                Contact(
                    user_id=user_id,
                    first_name="Hiring",
                    last_name="Team",
                    email=email,
                    linkedin_url=linkedin_url,
                    company=lead.company,
                    role=role,
                    notes=f"Imported from startup lead. Source: {lead.source_url}",
                )
            )
            inserted += 1
        db.commit()
    return {"inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import eligible startup leads into contacts.")
    parser.add_argument("user_id", type=int, help="Workspace user ID")
    args = parser.parse_args()
    print(json.dumps(import_startup_contacts(args.user_id), indent=2))
