PRAGMA foreign_keys = ON;

-- App accounts and Gmail draft quotas.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    full_name TEXT NOT NULL,
    google_id TEXT UNIQUE,
    refresh_token TEXT,
    user_type TEXT NOT NULL CHECK (user_type IN ('student', 'fresher', 'professional')),
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro', 'team')),
    daily_draft_limit INTEGER NOT NULL DEFAULT 10 CHECK (daily_draft_limit >= 0),
    daily_send_limit INTEGER NOT NULL DEFAULT 20 CHECK (daily_send_limit >= 1),
    gmail_connected INTEGER NOT NULL DEFAULT 0 CHECK (gmail_connected IN (0, 1)),
    professional_headline TEXT NOT NULL DEFAULT '',
    about TEXT NOT NULL DEFAULT '',
    human_approval_required INTEGER NOT NULL DEFAULT 1 CHECK (human_approval_required IN (0, 1)),
    followup_delay_days INTEGER NOT NULL DEFAULT 5 CHECK (followup_delay_days BETWEEN 1 AND 30),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Uploaded resume text and its parsing state.
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    skills_json TEXT NOT NULL DEFAULT '[]',
    file_content BLOB,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    parsed_at TEXT
);

-- Canonical skills used to describe resumes and job openings.
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    category TEXT NOT NULL
);

-- Skills extracted from a resume, with the extraction confidence.
CREATE TABLE IF NOT EXISTS resume_skills (
    resume_id INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    PRIMARY KEY (resume_id, skill_id)
);

-- Employers that own job openings and HR contacts.
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL COLLATE NOCASE UNIQUE,
    industry TEXT NOT NULL,
    size_band TEXT NOT NULL CHECK (size_band IN ('startup', 'small', 'medium', 'large', 'enterprise', 'unknown')),
    hq_city TEXT NOT NULL
);

-- HR contacts associated with a company and eligible for outreach.
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    job_title TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    linkedin_url TEXT,
    notes TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'employer' CHECK (category IN ('my', 'employer')),
    source TEXT NOT NULL,
    bounce_count INTEGER NOT NULL DEFAULT 0 CHECK (bounce_count >= 0),
    opted_out INTEGER NOT NULL DEFAULT 0 CHECK (opted_out IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Job openings and their associated HR contact.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    location TEXT NOT NULL,
    apply_url TEXT,
    apply_by TEXT,
    source_url TEXT,
    source_type TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    confidence INTEGER NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    notes TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    work_mode TEXT NOT NULL CHECK (work_mode IN ('onsite', 'hybrid', 'remote')),
    experience_level TEXT NOT NULL
        CHECK (experience_level IN ('intern', 'fresher', 'junior', 'mid', 'senior')),
    min_salary INTEGER CHECK (min_salary IS NULL OR min_salary >= 0),
    max_salary INTEGER CHECK (max_salary IS NULL OR max_salary >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    posted_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (min_salary IS NULL OR max_salary IS NULL OR max_salary >= min_salary)
);

-- Skills required or preferred for each job.
CREATE TABLE IF NOT EXISTS job_skills (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    is_required INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0, 1)),
    PRIMARY KEY (job_id, skill_id)
);

-- Personalized outreach drafts and their Gmail, delivery, and follow-up state.
CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resume_id INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    gmail_draft_id TEXT,
    thread_id TEXT,
    status TEXT NOT NULL DEFAULT 'drafted'
        CHECK (status IN ('drafted', 'sent', 'replied', 'bounced', 'closed')),
    drafted_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT,
    replied_at TEXT,
    follow_up_due TEXT,
    UNIQUE (user_id, contact_id, job_id)
);

-- Email addresses suppressed from all future outreach.
CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY COLLATE NOCASE,
    reason TEXT NOT NULL CHECK (reason IN ('opt_out', 'bounce', 'complaint', 'legal_request')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resumes_user_id ON resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_resume_skills_skill_id ON resume_skills(skill_id);
CREATE INDEX IF NOT EXISTS idx_contacts_company_id ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_owner_user_id ON contacts(owner_user_id);
DROP INDEX IF EXISTS idx_contacts_opted_out_email_status;
CREATE INDEX IF NOT EXISTS idx_contacts_opted_out ON contacts(opted_out);
CREATE INDEX IF NOT EXISTS idx_jobs_company_id ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_contact_id ON jobs(contact_id);
CREATE INDEX IF NOT EXISTS idx_jobs_is_active ON jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill_id ON job_skills(skill_id);
CREATE INDEX IF NOT EXISTS idx_outreach_resume_id ON outreach(resume_id);
CREATE INDEX IF NOT EXISTS idx_outreach_contact_id ON outreach(contact_id);
CREATE INDEX IF NOT EXISTS idx_outreach_job_id ON outreach(job_id);
CREATE INDEX IF NOT EXISTS idx_outreach_user_id_status ON outreach(user_id, status);
CREATE INDEX IF NOT EXISTS idx_outreach_user_id_follow_up_due ON outreach(user_id, follow_up_due);

-- Per-user visits used by the dashboard.
CREATE TABLE IF NOT EXISTS user_visits (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    visit_date TEXT NOT NULL
);

-- Gmail message history associated with outreach.
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    outreach_id INTEGER NOT NULL REFERENCES outreach(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('sent', 'received')),
    body TEXT NOT NULL,
    gmail_message_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Internal calendar reminders for outreach follow-ups.
CREATE TABLE IF NOT EXISTS follow_ups (
    id INTEGER PRIMARY KEY,
    outreach_id INTEGER NOT NULL REFERENCES outreach(id) ON DELETE CASCADE,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
);

-- A newly suppressed email is immediately opted out as an HR contact.
CREATE TRIGGER IF NOT EXISTS trg_suppression_opts_out_contact
AFTER INSERT ON suppressions
BEGIN
    UPDATE contacts
    SET opted_out = 1
    WHERE email = NEW.email COLLATE NOCASE;
END;
