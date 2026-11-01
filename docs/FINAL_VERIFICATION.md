# DatasetGuard final verification

Verification date: 2026-11-01

## Environment and services

- Python: 3.10.6
- Node.js: v22.14.0
- npm: 10.9.2
- Backend: FastAPI/Uvicorn, SQLite local store
- Frontend: React 18, TypeScript, Vite; production build completed
- `GET /api/health` returned HTTP 200 with `{"status":"ok","service":"DatasetGuard API","version":"1.0.0"}`; `/docs` returned HTTP 200.
- Port 8000 was occupied by a separate local application. DatasetGuard was run on `127.0.0.1:8001` for this verification, with Vite on `127.0.0.1:5173` and `VITE_DATASETGUARD_API_URL=http://127.0.0.1:8001/api`. The other process was left untouched.

## Backend tests

Command, run from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: **5 passed** (3.24 seconds). No failures.

## Frontend production build

Command, run from `frontend/`:

```powershell
npm run build
```

Result: **passed**. TypeScript build and Vite production build completed, transforming 2,381 modules.

| Build | JavaScript chunks | Gzip |
|---|---:|---:|
| Before chart lazy loading | 595.02 kB initial JS | 167.57 kB |
| After: initial app entry | 194.58 kB | 59.19 kB |
| After: deferred Charts chunk | 401.67 kB | 109.14 kB |
| After: total JS across both chunks | 596.25 kB | 168.33 kB |

The initial JavaScript download is 400.44 kB smaller (about 67.3%). Recharts is dynamically loaded with the dashboard charts, so pages that do not render those charts do not need that code in the initial entry. The total remains similar because the chart feature is retained.

## Browser and API workflow

Executed `npm run qa:e2e` with Google Chrome through Playwright, using a fresh headless browser context, frontend `http://127.0.0.1:5173/`, and DatasetGuard API `http://127.0.0.1:8001`.

**Passed:** registration; sample Customer Quality load and profile; dashboard analysis and charts; explorer/profile view; findings; individual finding evidence; cleaning; CSV upload; original-versus-cleaned comparison; ML baseline; CSV/PDF exports; logout and login. The comparison returned 6 shared features and 77 exact shared records. The cleaned CSV was downloaded and its expected header checked (2,960 bytes). The PDF was downloaded and its `%PDF` signature checked (3,470 bytes).

Browser diagnostics:

- Console errors: 0
- Uncaught page errors: 0
- Failed network requests: 0
- Unexpected HTTP error responses: 0
- One HTTP 401 was intentionally produced by the wrong-password login case; the UI displayed its error state, and the subsequent correct login succeeded.
- The responsive check passed at 390 px with document width 390 px. It exposed a picker/button overflow during QA; the mobile heading controls were constrained and the full workflow passed on rerun.

Machine-readable results, including the temporary test account, are in [browser-qa-results.json](screenshots/browser-qa-results.json). The run creates a temporary local account and test dataset records in the local database.

## Screenshots

All screenshots below were captured from the real application during the Playwright workflow with live backend data:

1. [Registration and login](screenshots/01-registration-login.png)
2. [Dataset upload and empty dashboard](screenshots/02-dataset-upload.png)
3. [Main dashboard](screenshots/03-main-dashboard.png)
4. [Dataset profiling](screenshots/04-dataset-profiling.png)
5. [Detection findings](screenshots/05-detection-findings.png)
6. [Finding evidence details](screenshots/06-problem-details.png)
7. [Cleaning studio](screenshots/07-cleaning-studio.png)
8. [Dataset comparison](screenshots/08-dataset-comparison.png)
9. [ML impact analysis](screenshots/09-model-impact.png)
10. [Reports and download actions](screenshots/10-reports.png)
11. [Mobile data explorer at 390 px](screenshots/11-mobile-layout.png)

There is no in-app PDF preview. The generated PDF itself was downloaded and checked for a PDF file signature.

## Startup commands

Use separate PowerShell terminals from the project directory.

Backend terminal (port 8001 is used here because 8000 was occupied during verification):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:DATASETGUARD_MAX_UPLOAD_MB = '50'
$env:DATASETGUARD_CORS_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173'
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Frontend terminal:

```powershell
cd frontend
npm ci
$env:VITE_DATASETGUARD_API_URL = 'http://127.0.0.1:8001/api'
npm run dev
```

Open <http://127.0.0.1:5173/>. API docs: <http://127.0.0.1:8001/docs>.

Reverification commands:

```powershell
# From backend/
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q

# From frontend/, with both servers running
npm run build
$env:DATASETGUARD_FRONTEND_URL = 'http://127.0.0.1:5173/'
$env:DATASETGUARD_API_URL = 'http://127.0.0.1:8001'
npm run qa:e2e
```

## Remaining limitations

- The deferred chart chunk is still about 402 kB minified; the optimization reduces initial load while retaining the charts.
- A PDF can be generated and downloaded, but there is no in-app PDF preview.
- Port 8000 may need to be changed if another local service uses it. Set the backend port and `VITE_DATASETGUARD_API_URL` to the same API origin.
- This verification covers local demonstration flows and the current five-test backend suite; it is not a production deployment or load test.
