from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

from app.detectors.engine import MISSING_TOKENS, analyze, compare_datasets, profile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FILES = DATA / "uploads"
FILES.mkdir(parents=True, exist_ok=True)
DB = DATA / "datasetguard.sqlite3"
MAX_UPLOAD = int(os.getenv("DATASETGUARD_MAX_UPLOAD_MB", "50")) * 1024 * 1024
app = FastAPI(title="DatasetGuard API", version="1.0.0", description="Dataset profiling, quality findings, cleaning and model impact experiments.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("DATASETGUARD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@contextmanager
def db():
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL, token TEXT);
        CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, original_path TEXT NOT NULL, clean_path TEXT, bytes INTEGER NOT NULL, created TEXT NOT NULL, target TEXT, findings TEXT, profile TEXT, health_score INTEGER, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS operations (id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id TEXT NOT NULL, user_id TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS experiments (id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id TEXT NOT NULL, user_id TEXT NOT NULL, target TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL);
        """)
        cols = {r[1] for r in c.execute("PRAGMA table_info(datasets)").fetchall()}
        if "health_score" not in cols: c.execute("ALTER TABLE datasets ADD COLUMN health_score INTEGER")


init_db()


def utc() -> str: return datetime.now(timezone.utc).isoformat()


def pw_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000).hex()


def current_user(authorization: str | None = Header(default=None)) -> sqlite3.Row:
    token = authorization.removeprefix("Bearer ") if authorization else ""
    with db() as c: row = c.execute("SELECT * FROM users WHERE token=?", (token,)).fetchone()
    if not row: raise HTTPException(401, "Sign in to continue.")
    return row


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class AnalyzeRequest(BaseModel): target: str | None = None


class DemoRequest(BaseModel): name: str


class CompareRequest(BaseModel):
    reference_id: str
    comparison_id: str


class CleanRequest(BaseModel):
    action: str
    columns: list[str] = []
    strategy: str = "median"
    rows: list[int] = []


class ExperimentRequest(BaseModel): target: str


def owned_dataset(dataset_id: str, user: sqlite3.Row) -> sqlite3.Row:
    with db() as c: row = c.execute("SELECT * FROM datasets WHERE id=? AND user_id=?", (dataset_id, user["id"])).fetchone()
    if not row: raise HTTPException(404, "Dataset not found.")
    return row


def load_frame(path: str) -> pd.DataFrame:
    p = Path(path)
    try:
        suffix = p.suffix.lower()
        if suffix == ".csv": return pd.read_csv(p, low_memory=False, keep_default_na=True)
        if suffix in {".xlsx", ".xls"}: return pd.read_excel(p)
        if suffix == ".json":
            try: return pd.read_json(p, lines=True)
            except ValueError: return pd.read_json(p)
        if suffix == ".parquet": return pd.read_parquet(p)
    except Exception as e: raise HTTPException(400, f"Could not parse dataset: {str(e)[:180]}")
    raise HTTPException(415, "Supported formats: CSV, XLSX, XLS, JSON, Parquet.")


def frame_for(row: sqlite3.Row) -> pd.DataFrame:
    return load_frame(row["clean_path"] or row["original_path"])


def dataset_json(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "bytes": row["bytes"], "created": row["created"], "target": row["target"], "cleaned": bool(row["clean_path"]), "health_score": row["health_score"]}


def simple_pdf(lines: list[str]) -> bytes:
    """Create a small, dependency-free PDF using a built-in Helvetica font."""
    pages = [lines[i:i + 48] for i in range(0, len(lines), 48)] or [[]]
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for index, page_lines in enumerate(pages):
        page_id = 4 + index * 2; content_id = page_id + 1; kids.append(f"{page_id} 0 R")
        text_ops = ["BT", "/F1 10 Tf", "48 744 Td", "14 TL"]
        for line in page_lines:
            safe = str(line).encode("ascii", "replace").decode("ascii").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            text_ops.append(f"({safe[:135]}) Tj T*" )
        text_ops.append("ET"); stream = "\n".join(text_ops).encode("latin-1")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"); offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out)); out.extend(f"{i} 0 obj\n".encode()); out.extend(obj); out.extend(b"\nendobj\n")
    xref = len(out); out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]: out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(out)


@app.get("/api/health")
def health(): return {"status": "ok", "service": "DatasetGuard API", "version": "1.0.0"}


@app.post("/api/auth/register")
def register(data: Credentials):
    email = data.email.strip().lower(); salt = secrets.token_bytes(16); user_id = secrets.token_hex(16); token = secrets.token_urlsafe(32)
    try:
        with db() as c: c.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, email, salt.hex(), pw_hash(data.password, salt), token))
    except sqlite3.IntegrityError: raise HTTPException(409, "An account with that email already exists.")
    return {"token": token, "email": email}


@app.post("/api/auth/login")
def login(data: Credentials):
    email = data.email.strip().lower()
    with db() as c: row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not secrets.compare_digest(pw_hash(data.password, bytes.fromhex(row["salt"])), row["password_hash"]): raise HTTPException(401, "Email or password is incorrect.")
    token = secrets.token_urlsafe(32)
    with db() as c: c.execute("UPDATE users SET token=? WHERE id=?", (token, row["id"]))
    return {"token": token, "email": email}


@app.post("/api/auth/logout")
def logout(user: sqlite3.Row = Depends(current_user)):
    with db() as c: c.execute("UPDATE users SET token=NULL WHERE id=?", (user["id"],))
    return {"ok": True}


@app.get("/api/me")
def me(user: sqlite3.Row = Depends(current_user)): return {"email": user["email"]}


@app.post("/api/datasets/upload")
def upload(payload: bytes = Body(..., media_type="application/octet-stream"), filename: str = Query(...), user: sqlite3.Row = Depends(current_user)):
    name = Path(filename or "dataset.csv").name
    ext = Path(name).suffix.lower()
    if ext not in {".csv", ".xlsx", ".xls", ".json", ".parquet"}: raise HTTPException(415, "Upload CSV, XLSX, XLS, JSON, or Parquet.")
    raw = payload
    if len(raw) > MAX_UPLOAD: raise HTTPException(413, f"File exceeds {MAX_UPLOAD // (1024*1024)} MB limit.")
    if not raw: raise HTTPException(400, "The uploaded file is empty.")
    dataset_id = secrets.token_hex(12); path = FILES / user["id"] / f"{dataset_id}{ext}"; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    try:
        df = load_frame(str(path))
        if df.empty: raise HTTPException(400, "Dataset has no records.")
        if len(df.columns) == 0 or len(df.columns) > 2000: raise HTTPException(400, "Dataset must have between 1 and 2,000 columns.")
    except Exception:
        path.unlink(missing_ok=True); raise
    p = profile(df)
    with db() as c: c.execute("INSERT INTO datasets(id,user_id,name,original_path,bytes,created,profile) VALUES(?,?,?,?,?,?,?)", (dataset_id,user["id"],name,str(path),len(raw),utc(),json.dumps(p)))
    return {"dataset": dataset_json(owned_dataset(dataset_id, user)), "profile": p, "preview": df.head(12).astype(object).where(pd.notna(df.head(12)), None).to_dict("records")}


@app.post("/api/datasets/demo")
def load_demo(req: DemoRequest, user: sqlite3.Row = Depends(current_user)):
    allowed = {"customer_quality.csv", "student_performance.csv", "classification_train.csv"}
    if req.name not in allowed: raise HTTPException(404, "Unknown demonstration dataset.")
    sample_dir = ROOT.parent / "sample_datasets"
    if not sample_dir.is_dir(): sample_dir = ROOT / "sample_datasets"
    source = sample_dir / req.name
    if not source.is_file(): raise HTTPException(404, "Demonstration data is unavailable.")
    dataset_id = secrets.token_hex(12); path = FILES / user["id"] / f"{dataset_id}.csv"; path.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, path)
    df = load_frame(str(path)); p = profile(df)
    with db() as c: c.execute("INSERT INTO datasets(id,user_id,name,original_path,bytes,created,profile) VALUES(?,?,?,?,?,?,?)", (dataset_id,user["id"],req.name,str(path),path.stat().st_size,utc(),json.dumps(p)))
    row = owned_dataset(dataset_id,user)
    return {"dataset": dataset_json(row), "profile": p, "preview": df.head(12).astype(object).where(pd.notna(df.head(12)), None).to_dict("records")}


@app.get("/api/datasets")
def list_datasets(user: sqlite3.Row = Depends(current_user)):
    with db() as c: rows = c.execute("SELECT * FROM datasets WHERE user_id=? ORDER BY created DESC", (user["id"],)).fetchall()
    return [dataset_json(r) for r in rows]


@app.post("/api/datasets/compare")
def compare(req: CompareRequest, user: sqlite3.Row = Depends(current_user)):
    if req.reference_id == req.comparison_id: raise HTTPException(400, "Choose two different datasets.")
    reference = owned_dataset(req.reference_id,user); comparison = owned_dataset(req.comparison_id,user)
    result = compare_datasets(frame_for(reference), frame_for(comparison))
    result["reference"] = reference["name"]; result["comparison"] = comparison["name"]
    return result


@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: str, user: sqlite3.Row = Depends(current_user)):
    row = owned_dataset(dataset_id, user); df = frame_for(row); p = profile(df)
    return {"dataset": dataset_json(row), "profile": p, "findings": json.loads(row["findings"] or "[]"), "preview": df.head(12).astype(object).where(pd.notna(df.head(12)), None).to_dict("records")}


@app.get("/api/datasets/{dataset_id}/records")
def records(dataset_id: str, offset: int = 0, limit: int = 50, user: sqlite3.Row = Depends(current_user)):
    row = owned_dataset(dataset_id, user); df = frame_for(row); limit = min(max(limit, 1), 200); offset = max(offset, 0)
    data = df.iloc[offset:offset+limit].astype(object).where(pd.notna(df.iloc[offset:offset+limit]), None).to_dict("records")
    return {"offset": offset, "limit": limit, "total": len(df), "columns": list(map(str, df.columns)), "records": data}


@app.post("/api/datasets/{dataset_id}/analyze")
def run_analysis(dataset_id: str, req: AnalyzeRequest, user: sqlite3.Row = Depends(current_user)):
    row = owned_dataset(dataset_id, user); df = frame_for(row)
    if req.target and req.target not in df.columns: raise HTTPException(400, "Target column does not exist.")
    findings = analyze(df, req.target)
    p = profile(df); weights = {"low": 1, "medium": 2, "high": 4}; risk = sum(weights[f["severity"]] * max(1, f["affected_records"]) for f in findings)
    denominator = max(1, len(df) * max(1, len(df.columns)) * 2); score = max(0, min(100, round(100 - 100 * risk / denominator)))
    report = {"health_score": score, "risk_level": "healthy" if score >= 85 else "watch" if score >= 65 else "elevated", "findings": findings, "analyzed_at": utc(), "target": req.target}
    with db() as c: c.execute("UPDATE datasets SET target=?, findings=?, profile=?, health_score=? WHERE id=? AND user_id=?", (req.target, json.dumps(findings), json.dumps(p), score, dataset_id, user["id"]))
    return report


@app.post("/api/datasets/{dataset_id}/clean")
def clean(dataset_id: str, req: CleanRequest, user: sqlite3.Row = Depends(current_user)):
    row = owned_dataset(dataset_id, user); df = frame_for(row); before = len(df); detail: dict[str, Any] = {"action": req.action, "columns": req.columns}
    if req.action == "drop_duplicates":
        df = df.drop_duplicates().reset_index(drop=True); detail["rows_removed"] = before-len(df)
    elif req.action == "impute":
        cols = req.columns or list(df.columns)
        for col in cols:
            if col not in df.columns: raise HTTPException(400, f"Unknown column: {col}")
            missing_tokens = df[col].astype("string").str.strip().str.lower().isin(MISSING_TOKENS)
            df.loc[missing_tokens.fillna(False), col] = pd.NA
            if pd.api.types.is_numeric_dtype(df[col]):
                value = df[col].mean() if req.strategy == "mean" else df[col].median()
            else:
                mode = df[col].mode(dropna=True); value = mode.iloc[0] if not mode.empty else "Unknown"
            df[col] = df[col].fillna(value)
        detail["strategy"] = req.strategy
    elif req.action == "trim":
        cols = req.columns or [str(c) for c in df.select_dtypes(include=["object", "string"]).columns]
        for col in cols:
            if col not in df.columns: raise HTTPException(400, f"Unknown column: {col}")
            df[col] = df[col].astype("string").str.strip().replace("<NA>", pd.NA)
    elif req.action == "drop_rows":
        ids = sorted({i for i in req.rows if 0 <= i < len(df)}, reverse=True); df = df.drop(index=ids).reset_index(drop=True); detail["rows_removed"] = len(ids)
    else: raise HTTPException(400, "Supported actions: impute, drop_duplicates, trim, drop_rows.")
    path = FILES / user["id"] / f"{dataset_id}-clean.csv"; df.to_csv(path, index=False)
    with db() as c:
        c.execute("UPDATE datasets SET clean_path=?, findings=NULL, profile=?, health_score=NULL WHERE id=?", (str(path), json.dumps(profile(df)), dataset_id))
        c.execute("INSERT INTO operations(dataset_id,user_id,action,details,created) VALUES(?,?,?,?,?)", (dataset_id,user["id"],req.action,json.dumps(detail),utc()))
    return {"ok": True, "rows_before": before, "rows_after": len(df), "profile": profile(df), "message": "Cleaning applied to a working copy. The original upload is preserved."}


@app.get("/api/datasets/{dataset_id}/cleaning-history")
def cleaning_history(dataset_id: str, user: sqlite3.Row = Depends(current_user)):
    owned_dataset(dataset_id, user)
    with db() as c: rows = c.execute("SELECT action,details,created FROM operations WHERE dataset_id=? AND user_id=? ORDER BY id DESC", (dataset_id,user["id"])).fetchall()
    return [{"action": r["action"], "details": json.loads(r["details"]), "created": r["created"]} for r in rows]


@app.get("/api/datasets/{dataset_id}/export")
def export_dataset(dataset_id: str, user: sqlite3.Row = Depends(current_user)):
    row = owned_dataset(dataset_id,user); df = frame_for(row); buffer = io.StringIO(); df.to_csv(buffer,index=False); buffer.seek(0)
    name = Path(row["name"]).stem + ("-cleaned" if row["clean_path"] else "-dataset") + ".csv"
    return Response(content=buffer.getvalue().encode("utf-8"), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/experiments")
def experiment(req: ExperimentRequest, dataset_id: str, user: sqlite3.Row = Depends(current_user)):
    row = owned_dataset(dataset_id,user); df = frame_for(row)
    if req.target not in df.columns: raise HTTPException(400, "Choose an existing target column.")
    data = df.dropna(subset=[req.target]).copy()
    if len(data) < 20: raise HTTPException(400, "At least 20 labeled rows are needed for a train/test comparison.")
    y = data.pop(req.target); X = pd.get_dummies(data, dummy_na=True)
    X = X.replace([float("inf"),float("-inf")],pd.NA).fillna(X.median(numeric_only=True)).fillna(0)
    if X.shape[1] == 0: raise HTTPException(400, "No usable features remain after selecting the target.")
    classification = y.nunique() <= max(20, int(len(y)*.05)) or not pd.api.types.is_numeric_dtype(y)
    if classification:
        y = y.astype(str); counts = y.value_counts(); valid = counts[counts >= 2].index; keep = y.isin(valid); X=X.loc[keep]; y=y.loc[keep]
        if y.nunique() < 2 or len(y)<20: raise HTTPException(400,"Classification needs at least two classes with two rows each.")
        encoded=LabelEncoder().fit_transform(y); strat=encoded if min(pd.Series(encoded).value_counts()) >= 2 else None
    else: encoded = pd.to_numeric(y, errors="coerce"); valid=encoded.notna(); X=X.loc[valid]; encoded=encoded[valid]; strat=None
    try: Xtr,Xte,ytr,yte=train_test_split(X,encoded,test_size=.25,random_state=41,stratify=strat)
    except ValueError as e: raise HTTPException(400,f"Unable to create a reliable split: {e}")
    model = RandomForestClassifier(n_estimators=120,random_state=41,class_weight="balanced") if classification else RandomForestRegressor(n_estimators=120,random_state=41)
    model.fit(Xtr,ytr); pred=model.predict(Xte)
    metrics = {"accuracy": round(float(accuracy_score(yte,pred)),4), "f1_macro": round(float(f1_score(yte,pred,average="macro",zero_division=0)),4)} if classification else {"r2":round(float(r2_score(yte,pred)),4),"mae":round(float(mean_absolute_error(yte,pred)),4)}
    result={"target":req.target,"model":"Random Forest","task":"classification" if classification else "regression","train_rows":len(Xtr),"test_rows":len(Xte),"features":X.shape[1],"metrics":metrics,"split":"75/25 holdout; fixed random seed 41","note":"Baseline on the current dataset copy. This is not a causal estimate of cleaning impact; compare repeated experiments before drawing conclusions."}
    with db() as c:c.execute("INSERT INTO experiments(dataset_id,user_id,target,result,created) VALUES(?,?,?,?,?)",(dataset_id,user["id"],req.target,json.dumps(result),utc()))
    return result


@app.get("/api/experiments")
def experiments(dataset_id: str, user: sqlite3.Row = Depends(current_user)):
    owned_dataset(dataset_id,user)
    with db() as c: rows=c.execute("SELECT result,created FROM experiments WHERE dataset_id=? AND user_id=? ORDER BY id DESC",(dataset_id,user["id"])).fetchall()
    return [{**json.loads(r["result"]),"created":r["created"]} for r in rows]


@app.post("/api/reports/generate")
def report(dataset_id: str, user: sqlite3.Row = Depends(current_user)):
    row=owned_dataset(dataset_id,user); df=frame_for(row); findings=json.loads(row["findings"] or "[]"); p=profile(df)
    lines=["DatasetGuard audit report",f"Dataset: {row['name']}",f"Generated: {utc()}",f"Shape: {p['rows']:,} rows x {p['columns']} columns | Missing cells: {p['missing']:,}",f"Health score: {row['health_score'] if row['health_score'] is not None else 'not calculated'}","",f"Column profile ({p['columns']})"]
    for column in p["column_profiles"]:
        text=f"{column['name']} | {column['dtype']} | missing {column['missing_pct']:.1f}% | unique {column['unique']}"
        if column.get("kind")=="numeric" and column.get("stats"):
            stats=column["stats"]; text += f" | mean {column.get('mean')} | median {column.get('median')} | min {stats.get('min')} | max {stats.get('max')}"
        lines.append(text)
    lines.extend(["",f"Findings ({len(findings)})"])
    if not findings: lines.append("No findings saved yet. Run analysis to populate this report.")
    for f in findings:
        line=f"[{f['severity'].upper()}] {f['title']} — {f['affected_records']} affected records"
        lines.append(line); lines.append(f"Method: {f['method']} | Confidence: {f['confidence']:.0%}")
        lines.append(f"Recommendation: {f['recommendation']}")
    lines.extend(["", "Interpret findings with domain context; anomaly and correlation flags are not proof of error."])
    filename=Path(row["name"]).stem+"-datasetguard-report.pdf"
    return Response(content=simple_pdf(lines),media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{filename}"'})

