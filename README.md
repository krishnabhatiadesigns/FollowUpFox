# Follow Up Fox

Follow Up Fox is a full-stack outreach and job-search assistant. It combines a React frontend with a FastAPI backend to help users manage contacts, prepare outreach, track follow-ups, and find employers that match their resumes.

## Features

- Google sign-in and Gmail outreach, with the user approving messages before they are sent
- Contact and outreach tracking, dashboard metrics, and scheduled internal follow-up reminders
- Resume upload and analysis, with employer matching and saved-resume preview
- Startup leads and job postings, plus an AI-assisted metrics query endpoint
- Firebase Realtime Database for persistent application data

## Project structure

- `src/` — React frontend
- `backend/app/` — FastAPI application and data services
- `backend/tests/` — backend API tests
- `db/` — database schema, seed data, and hiring-data importer
- `dataconnect/` — Firebase Data Connect configuration

## Requirements

- Node.js and npm
- Python and pip
- A Firebase Realtime Database and service account for backend data persistence
- Google OAuth credentials and Gmail API enabled for sign-in and email sending

Groq credentials are optional. Without them, resume skill extraction uses a local fallback.

## Run locally

Run the backend and frontend in separate terminals from the project root.

### Backend

```powershell
python -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
```

Edit `backend\.env` and configure the required Firebase settings and credentials. Then start the API:

```powershell
python -m uvicorn backend.app.main:app --reload --port 8000
```

The API health check is at `http://localhost:8000/health`; interactive API documentation is at `http://localhost:8000/docs`.

### Frontend

```powershell
npm install
npm run dev
```

Vite prints the local frontend URL when it starts (normally `http://localhost:5173`). The frontend uses `http://localhost:8000` as its default API URL. To use a different backend, create a root `.env.local` file with:

```dotenv
VITE_API_URL=http://localhost:8000
```

## Backend configuration

`backend\.env.example` lists the available settings. In particular:

- `FIREBASE_DATABASE_URL` and `FIREBASE_SERVICE_ACCOUNT_PATH` connect the backend to Firebase. Keep the service-account JSON file local and out of version control.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` configure Google OAuth. Add `http://localhost:8000/auth/google/callback` as an authorized redirect URI in Google Cloud.
- `SESSION_SECRET` should be a long, random value.
- `TOKEN_ENCRYPTION_KEY` should be a persistent Fernet key. Generate one with:

  ```powershell
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

- `FRONTEND_URL` should match the frontend origin, normally `http://localhost:5173`.
- `GROQ_API_KEY` and `GROQ_MODEL` are optional and enable Groq-backed AI features.

`OAUTHLIB_INSECURE_TRANSPORT=1` is for local development only; do not enable it in production. Never commit `.env` files, service-account credentials, or other secrets.

## Data storage

Firebase Realtime Database is the persistent store. The backend uses a request-local in-memory SQLite session for relational operations and synchronizes data with Firebase. Existing local SQLite database files are not automatically imported.

See [backend/README.md](backend/README.md) for detailed API routes, data import, startup ingestion, employer matching, and OAuth setup instructions.

## Validation

Run the backend tests:

```powershell
python -m pytest -q backend\tests\test_api.py
```

Build the frontend for production:

```powershell
npm run build
```
