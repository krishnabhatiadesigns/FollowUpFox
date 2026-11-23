# Follow Up Fox API

FastAPI backend for Google sign-in, outreach metrics, Gmail sending, internal follow-ups, and AI queries. Firebase Realtime Database is the persistent data store.

## Run locally

From the project root:

```powershell
python -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
python -m uvicorn backend.app.main:app --reload --port 8000
```

The health check is available at `http://localhost:8000/health`.

## Google setup

1. Create a Google Cloud project.
2. Enable Gmail API.
3. Configure the OAuth consent screen and add your account as a test user.
4. Create a Web OAuth client.
5. Add `http://localhost:8000/auth/google/callback` as an authorized redirect URI.
6. Put the client ID, client secret, a persistent Fernet key, and Firebase Realtime Database settings in `backend\.env`.
7. Create a Firebase service account with Realtime Database access and set `FIREBASE_SERVICE_ACCOUNT_PATH` to its local JSON file. Do not commit the service-account file. Set `FIREBASE_DATABASE_URL` to the database URL from the Firebase console.

Generate a Fernet key with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The frontend can start the flow by navigating to `http://localhost:8000/auth/google/login`. After consent, the backend creates the user, stores the refresh token encrypted, and redirects to the Vite app. Calendar follow-ups are stored internally; no Calendar or Tasks permissions are requested.

## Database

The backend stores persistent records in Firebase Realtime Database under top-level nodes named for the existing tables (`users`, `companies`, `contacts`, `jobs`, `outreach`, and so on). Rows are keyed by their existing primary IDs; foreign keys remain IDs in each row. Binary resume files are base64-encoded in Firebase and decoded for application use.

The API uses a request-local in-memory SQLite session to preserve existing relational joins and constraints while Firebase remains the only persistent data store. Each request loads records from Firebase and writes its changed rows back before completing. Configure Firebase credentials before starting the API. Existing SQLite databases are not read or migrated automatically. To import a table export, use `python -m backend.app.firebase_import path\to\export.json` after reviewing the export and target database. The hiring CSV importer also writes through the Firebase-backed session: `python -m db.import_hiring_csv path\to\hiring_employers_contacts.csv`.

## Main API routes

- `GET /auth/google/login` and `GET /auth/google/callback` — Google OAuth
- `GET /auth/me` and `POST /auth/logout` — session identity
- `GET /dashboard/stats` — contacted, replied, awaiting, and response-rate metrics
- `GET/POST /contacts` — contact management
- `POST /resume/analyze` — upload a PDF, TXT, or Markdown resume (maximum 10 MB), extract skills, and persist matched employers; the original file is stored as base64 data in Firebase
- `GET /resume/latest/file` — securely preview the signed-in user's saved resume
- `GET /employers` — return only employers matched to the signed-in user's latest resume analysis
- `POST /drafts/suggest` — personalized draft suggestion
- `POST /outreach/send` — human-approved Gmail send
- `GET /calendar/events` — list internal follow-up reminders for a date range; reminders are scheduled automatically after sending outreach using the configured follow-up delay
- `POST /ai/query` — natural-language metrics queries, with optional OpenAI-compatible provider enhancement
- `GET /startups` — return sourced startup leads and their current job postings for the signed-in user

## Groq setup

The backend uses LangChain's `ChatGroq` integration with OpenAI's GPT-OSS model hosted by Groq. Add these values to `backend\.env`:

```dotenv
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=openai/gpt-oss-20b
```

`GROQ_MODEL` can be set to another model supported by your Groq account. Restart Uvicorn after changing `.env`.

## Employer matching

The backend creates ten demo employer records on first startup in the configured database. Resume skills are extracted with Groq when configured, with a deterministic local fallback otherwise. Employer records and per-user resume matches are stored in `employers`, `resume_analyses`, and `employer_matches`; replace the demo records with the production employer dataset before deployment.

## Daily startup ingestion

The startup collector stores sourced records in `startup_leads` and `startup_jobs`. It accepts only records with an official domain and source URL:

```powershell
python -m backend.app.startup_ingestion path\to\startup-batch.json
```

The JSON input can be an array or an object with a `companies` array. Each company should include `company`, `official_domain`, `website`, `source_url`, `source_type`, and an integer `confidence` from 0 to 100. Each accepted company must include either a public role-based `contact_email` or a `linkedin_url`; records with neither are skipped and reported in the command output. Optional `contact_category`, other contact data, and `jobs` can be included. Records are upserted by official domain and jobs by canonical posting URL.

The daily automation must use official APIs or permission-based crawling, respect `robots.txt` and terms, collect only publicly listed role-based addresses, preserve source URLs, and leave uncertain records with `review_status: "pending"`. It must not send outreach or infer personal email addresses.

Contacts may have a public role-based email, a LinkedIn URL, or both. Startup leads imported into contacts use `Hiring Team` when no person name is available and are deduplicated per user by company plus email or LinkedIn URL.

The email draft UI offers an unchecked-by-default option to attach the saved resume. The selected original file is attached only when the user approves sending. Existing resume analyses created before original-file storage was added must be uploaded again to use this option.
