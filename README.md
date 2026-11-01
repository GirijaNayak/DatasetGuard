# DatasetGuard

**Detect. Diagnose. Improve. Trust Your Data.**

DatasetGuard is a local-first dataset quality and reliability application. It profiles tabular files, runs deterministic data checks, gives row-level evidence and recommendations, applies explicit cleaning operations to a working copy, and exports CSV/PDF artifacts. It also provides a reproducible Random Forest baseline for a selected target.

## Run locally

Verified locally with Python 3.10.6, Node.js 22.14.0, and npm 10.9.2. Python 3.10+ and Node.js 20+ are required. Run the API and UI in separate PowerShell terminals.

1. Install backend dependencies and start the API:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
    python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

   The API reads `DATASETGUARD_MAX_UPLOAD_MB` (default `50`) and `DATASETGUARD_CORS_ORIGINS` (defaults to the two localhost Vite origins) from the environment. Copy `.env.example` as a reference and set variables in the shell before launching Uvicorn. The local SQLite database and upload files are created under `backend/data/` and `backend/storage/`.

2. Install frontend packages and start the UI in a second terminal:

   ```powershell
   cd frontend
    npm ci
   npm run dev
   ```

3. Open <http://127.0.0.1:5173>. Create a local account, upload a file, or choose one of the sample datasets. API docs are at <http://127.0.0.1:8000/docs>.

The UI API base defaults to `http://127.0.0.1:8000/api`. To use another port, set `VITE_DATASETGUARD_API_URL` before starting Vite (the value is read at startup):

```powershell
$env:VITE_DATASETGUARD_API_URL = 'http://127.0.0.1:8001/api'
npm run dev
```

Copy `frontend/.env.example` to `frontend/.env.local` to configure this persistently. For local verification in this environment, port 8000 was occupied by an unrelated application, so DatasetGuard ran on API port 8001 with the UI pointed at that API. Do not stop that other process; use the port override above.

For a one-step API launch after dependencies are installed, run `backend\run.ps1`. The frontend is a Vite development app. Files and SQLite data live under `backend/data/` and are not checked into source control.

To run both services in Docker, use `docker compose up --build` from the project root and open <http://localhost:8080>.

## What it does

- CSV, XLSX/XLS, JSON, and Parquet ingestion (50 MB default limit).
- Per-column type, missingness, uniqueness and numeric/category summaries; numeric correlations; paginated data explorer.
- Missing tokens, exact duplicate rows, univariate IQR outliers, multivariate Isolation Forest candidates, whitespace/category variants, high numeric correlations, optional target class imbalance, rare-label review cues, and target leakage candidates.
- Compare two owned dataset versions with Kolmogorov–Smirnov tests for numeric features, chi-square tests and Jensen–Shannon effect sizes for categorical features, PSI for numeric features, and exact shared-record counts.
- Findings include affected columns and row indices, method, evidence, severity, confidence, recommendation, and limitations.
- A health score is calculated by the backend from the current findings and severity/affected-record weighting. It is an operational summary, not a calibrated probability.
- Explicit median/mean/mode imputation, exact duplicate removal, and whitespace trimming operate on a working CSV copy. The original upload is kept.
- CSV export, PDF audit report, and Random Forest baseline experiment with a fixed 75/25 holdout seed.
- Local registration/login using PBKDF2 password hashing, bearer sessions, and user-scoped dataset queries.

## Architecture

- `frontend/`: React + TypeScript + Vite UI, Recharts charts, Lucide icons, custom responsive CSS.
- `backend/app/main.py`: FastAPI routes, SQLite persistence, upload/clean/export/report/model workflow.
- `backend/app/detectors/engine.py`: profile calculations and independent, deterministic checks.
- `sample_datasets/`: three synthetic datasets and a ground-truth issue manifest.
- `backend/data/`: generated local database and per-user files.

SQLite is the local development store. Uploaded bytes stay in the filesystem; the database stores metadata and paths. FastAPI's OpenAPI interface documents every implemented endpoint.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | Local user sessions |
| GET | `/api/me` | Current account |
| POST | `/api/datasets/upload` | Upload and profile a file |
| POST | `/api/datasets/demo` | Load a named sample into the current account |
| GET | `/api/datasets`, `/api/datasets/{id}` | Dataset history and detail |
| GET | `/api/datasets/{id}/records?offset=0&limit=50` | Paginated data rows |
| POST | `/api/datasets/compare` | Compare `{ "reference_id": "...", "comparison_id": "..." }` |
| POST | `/api/datasets/{id}/analyze` | Run findings, optionally with `{ "target": "label" }` |
| POST | `/api/datasets/{id}/clean` | Apply `{action: impute, drop_duplicates, trim, drop_rows}` |
| GET | `/api/datasets/{id}/cleaning-history` | Cleaning operation log |
| GET | `/api/datasets/{id}/export` | Download current CSV |
| POST | `/api/experiments?dataset_id={id}` | Fit and persist target baseline |
| GET | `/api/experiments?dataset_id={id}` | Experiment history |
| POST | `/api/reports/generate?dataset_id={id}` | Download PDF audit report |

## Demonstration workflow

1. Create an account and select **Customer quality** on the empty dashboard.
2. Run analysis. Inspect the missing `annual_spend`, duplicate row, age anomaly and category variants in Findings.
3. Open a finding for evidence and row indices. Apply imputation or remove exact duplicates from Cleaning Studio.
4. Export the current working copy and generate a PDF report.
5. Select `segment` as target and run a model baseline from Model Impact.

The Student Performance sample demonstrates missingness, target imbalance, and label case variants. The classification sample contains intentional label noise and a target-separating encoded feature. Upload `classification_shifted.csv` as a second dataset, then use **Compare datasets** to see the actual feature shift and overlapping-record checks.

## Verification

From `backend/`, install the test dependency and run the backend suite:

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

From `frontend/`, install the locked dependencies and build the production frontend:

```powershell
npm ci
npm run build
```

With both local services running and the API base set correctly in Vite, run the Chrome/Playwright workflow:

```powershell
$env:DATASETGUARD_FRONTEND_URL = 'http://127.0.0.1:5173/'
$env:DATASETGUARD_API_URL = 'http://127.0.0.1:8001'
npm run qa:e2e
```

The browser workflow uses a fresh headless Chrome context, registers a temporary local account, exercises the sample/upload, analysis, finding evidence, cleaning, comparison, model, export and auth flows, checks the 390 px layout, and saves genuine application screenshots under `docs/screenshots/`. It requires Google Chrome installed; `DATASETGUARD_CHROME` can point to another Chrome executable.

## Screenshots

Captured from the verified local browser run using real API data:

- Registration and login: [01-registration-login.png](docs/screenshots/01-registration-login.png)
- Dataset upload: [02-dataset-upload.png](docs/screenshots/02-dataset-upload.png)
- Main dashboard: [03-main-dashboard.png](docs/screenshots/03-main-dashboard.png)
- Dataset profiling: [04-dataset-profiling.png](docs/screenshots/04-dataset-profiling.png)
- Detection findings: [05-detection-findings.png](docs/screenshots/05-detection-findings.png)
- Finding evidence details: [06-problem-details.png](docs/screenshots/06-problem-details.png)
- Cleaning studio: [07-cleaning-studio.png](docs/screenshots/07-cleaning-studio.png)
- Dataset comparison: [08-dataset-comparison.png](docs/screenshots/08-dataset-comparison.png)
- ML impact: [09-model-impact.png](docs/screenshots/09-model-impact.png)
- Reports and export controls: [10-reports.png](docs/screenshots/10-reports.png)
- Mobile data explorer: [11-mobile-layout.png](docs/screenshots/11-mobile-layout.png)

The application generates and downloads a PDF; it does not currently provide an in-app PDF preview.

See [docs/FINAL_VERIFICATION.md](docs/FINAL_VERIFICATION.md) for observed test results, bundle measurements, and known limitations.

## Limitations

- This implementation is designed for local demonstrations and small/medium tabular datasets. Detection runs synchronously and the default upload cap is 50 MB.
- XLS is read through Pandas' installed Excel engine; Parquet requires PyArrow.
- Browser screenshots use desktop-sized viewports except the dedicated 390 px mobile capture. No in-app PDF preview is implemented.
- DBSCAN/LOF, VIF, Cleanlab, SHAP, autoencoders, external AI assistance, password reset, and PostgreSQL deployment are not implemented in this core release.
- Model experiments are a reproducible baseline, not a causal before/after cleaning analysis. The UI labels the limitations explicitly.
- Anomaly, correlation, and model-derived signals are candidates for human review, not proof of an error.
- Local bearer tokens are one active session per account. Deployments exposed to other users need hardened secret/session management, rate limits, CSRF policy, and a production storage configuration.

## Future extensions

Background jobs and progress reporting, split-aware leakage checks, Cleanlab-assisted label review, PostgreSQL migrations, advanced multivariate detectors, configurable validation rules, and repeated paired model experiments.
