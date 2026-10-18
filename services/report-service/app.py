"""Report Service: stores review results in PostgreSQL and serves them to the dashboard."""
import logging
import os
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("report-service")

DSN = (f"host={os.getenv('POSTGRES_HOST', 'localhost')} port={os.getenv('POSTGRES_PORT', '5432')} "
       f"dbname={os.getenv('POSTGRES_DB', 'reviews')} user={os.getenv('POSTGRES_USER', 'reviewer')} "
       f"password={os.getenv('POSTGRES_PASSWORD', '')}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id               SERIAL PRIMARY KEY,
    job_id           TEXT,
    repo             TEXT NOT NULL,
    pr_number        INTEGER NOT NULL,
    pr_title         TEXT,
    pr_url           TEXT,
    head_sha         TEXT,
    author           TEXT,
    demo             BOOLEAN DEFAULT FALSE,
    files_analyzed   INTEGER DEFAULT 0,
    total_issues     INTEGER DEFAULT 0,
    errors           INTEGER DEFAULT 0,
    warnings         INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0,
    ai_summary       TEXT,
    findings         JSONB DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ DEFAULT now()
);
"""

LIST_COLUMNS = ("id, repo, pr_number, pr_title, pr_url, head_sha, author, demo, files_analyzed, "
                "total_issues, errors, warnings, duration_seconds, created_at")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


@asynccontextmanager
async def lifespan(_app):
    for attempt in range(1, 31):  # Postgres may still be starting up
        try:
            with connect() as conn:
                conn.execute(SCHEMA)
            log.info("Database ready")
            break
        except psycopg.OperationalError as exc:
            log.warning("Database not ready (attempt %s): %s", attempt, exc)
            time.sleep(2)
    else:
        raise RuntimeError("Could not connect to PostgreSQL")
    yield


app = FastAPI(title="Report Service", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


@app.get("/health")
def health():
    return {"status": "ok"}


# Called by the Code Analysis Service inside the cluster. The gateway does not expose /internal.
@app.post("/internal/reports", status_code=201)
def save_report(result: dict):
    params = {
        "job_id": result.get("job_id"), "repo": result["repo"], "pr_number": result["pr_number"],
        "pr_title": result.get("pr_title"), "pr_url": result.get("pr_url"),
        "head_sha": result.get("head_sha"), "author": result.get("author"),
        "demo": bool(result.get("demo")), "files_analyzed": result.get("files_analyzed", 0),
        "total_issues": result.get("total_issues", 0), "errors": result.get("errors", 0),
        "warnings": result.get("warnings", 0), "duration_seconds": result.get("duration_seconds", 0),
        "ai_summary": result.get("ai_summary"), "findings": Jsonb(result.get("findings", [])),
    }
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO reports (job_id, repo, pr_number, pr_title, pr_url, head_sha, author, demo,
                   files_analyzed, total_issues, errors, warnings, duration_seconds, ai_summary, findings)
               VALUES (%(job_id)s, %(repo)s, %(pr_number)s, %(pr_title)s, %(pr_url)s, %(head_sha)s,
                   %(author)s, %(demo)s, %(files_analyzed)s, %(total_issues)s, %(errors)s, %(warnings)s,
                   %(duration_seconds)s, %(ai_summary)s, %(findings)s)
               RETURNING id""",
            params,
        ).fetchone()
    return {"id": row["id"]}


# --- Public endpoints: the gateway checks the JWT with the Auth Service before forwarding ---
@app.get("/api/reports")
def list_reports(limit: int = 50):
    limit = max(1, min(limit, 200))
    with connect() as conn:
        return conn.execute(f"SELECT {LIST_COLUMNS} FROM reports ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


@app.get("/api/reports/stats")
def stats():
    with connect() as conn:
        return conn.execute(
            """SELECT COUNT(*) AS reviews,
                      COALESCE(SUM(total_issues), 0) AS total_issues,
                      COALESCE(SUM(errors), 0) AS errors,
                      COALESCE(SUM(warnings), 0) AS warnings,
                      COALESCE(ROUND(AVG(duration_seconds)::numeric, 2), 0)::float AS avg_duration
               FROM reports"""
        ).fetchone()


@app.get("/api/reports/{report_id}")
def get_report(report_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = %s", (report_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return row
