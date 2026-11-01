# DatasetGuard architecture notes

## Request flow

The React app calls the FastAPI JSON API with a bearer token. Upload parsing and profiling happen server-side. The server stores the original file in a per-user directory and stores metadata in SQLite. The detection engine accepts a Pandas DataFrame and returns structured findings. Cleaning writes a new working CSV; subsequent preview, analysis, and export use that copy. Reports and model experiments use the same current copy.

## Scoring

The backend computes a bounded 0–100 health summary from finding severity and affected-record counts normalized by dataset size. It is an operational prioritization score and is not trained, calibrated, or interpretable as a probability of data correctness.

## Data protection boundaries

All dataset routes require the current session and filter lookups by its user ID. Upload names are reduced to basenames, formats are allow-listed, size is limited, and file content is parsed as data only. Original uploads are not overwritten by cleaning operations.

## Implemented checks

Nulls and common missing tokens, entirely empty columns, exact duplicate rows, robust IQR candidates, Isolation Forest multivariate candidates, whitespace and case-folded category variants, Pearson feature pairs with absolute correlation ≥ 0.95, optional target class frequency ratio, rare-label review cues, target feature association scans, and two-file distribution comparison using KS/chi-square tests with effect sizes. The checks are deliberately conservative in wording: anomalies, shifts, and correlations are findings for review, not confirmed defects.
