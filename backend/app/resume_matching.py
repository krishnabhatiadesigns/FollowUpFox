from __future__ import annotations

import io
import json
import re
from pathlib import Path


SKILL_VOCABULARY = {
    "python", "javascript", "typescript", "react", "node.js", "sql", "figma",
    "product design", "ux design", "ui design", "user research", "design systems",
    "prototyping", "wireframing", "usability testing", "product management",
    "data analysis", "machine learning", "python", "aws", "docker", "kubernetes",
    "communication", "project management", "java", "c++", "go", "graphql",
}
SKILL_ALIASES = {
    "node.js": ("node.js", "nodejs", "node js"),
    "react": ("react", "react.js"),
}
JOB_SKILL_VOCABULARY = {
    "python", "javascript", "typescript", "react", "node.js", "sql", "aws",
    "docker", "kubernetes", "java", "c++", "go", "graphql",
}


def _contains_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text.lower()))


def canonical_skill(skill: str) -> str:
    normalized = skill.strip().lower()
    for canonical, aliases in SKILL_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized


def extract_resume_text(file_bytes: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        import fitz

        document = fitz.open(stream=file_bytes, filetype="pdf")
        text = "\n\n".join(page.get_text("text") for page in document)
    elif suffix in {".txt", ".md"}:
        text = file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError("Supported resume formats are PDF, TXT, and MD")
    text = text.strip()
    if not text:
        raise ValueError("No selectable text was found in the uploaded resume")
    return text


def fallback_skills(text: str) -> list[str]:
    normalized = text.lower()
    return sorted(
        canonical_skill(skill)
        for skill in SKILL_VOCABULARY
        if any(_contains_term(normalized, alias) for alias in SKILL_ALIASES.get(skill, (skill,)))
    )


def infer_job_skills(title: str, description: str) -> set[str]:
    title_text = title.lower()
    searchable_text = f"{title} {description}".lower()
    skills = {
        canonical_skill(skill)
        for skill in JOB_SKILL_VOCABULARY
        if any(_contains_term(searchable_text, alias) for alias in SKILL_ALIASES.get(skill, (skill,)))
    }
    if re.search(r"\b(web|frontend|front-end|full[\s-]?stack)\b", title_text):
        skills.add("web development")
    return skills


def parse_skill_response(content: str) -> list[str]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    start = cleaned.find("{")
    if start >= 0:
        value = json.JSONDecoder().raw_decode(cleaned[start:])[0]
        skills = value.get("skills", []) if isinstance(value, dict) else []
    else:
        skills = []
    return sorted({canonical_skill(str(skill)) for skill in skills if str(skill).strip()})[:50]
