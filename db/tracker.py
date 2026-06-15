# tracker.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
SQLite-backed application tracker.

Public functions:
    log_application(...)   - records one job application
    load_seen_urls()       - every job URL/ID ever logged (for dedup)
    load_applied_urls()    - only URLs/IDs of jobs actually applied to
    show_stats()           - prints a summary of all applications
    export_to_csv(path)    - writes the whole table to a CSV file
"""
import os
import csv
import re
import json
import sqlite3
from datetime import datetime

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
try:
    from core.logger import log, log_warn, log_error
except Exception:
    def log(m,*a): pass
    def log_warn(m,*a): pass
    def log_error(m,*a,**k): pass

# -- Database location -------------------------------------------
# Use the configured output directory if local_settings is present,
# otherwise fall back to a local "output" folder.
try:
    from core.settings import get_output_dir
    _OUTPUT_DIR = get_output_dir()
except Exception:
    _OUTPUT_DIR = "output"

DB_FILE = os.path.join(_OUTPUT_DIR, "applications.db")
TABLE   = "applications"

# -- Column definitions ------------------------------------------
# (column_name, sqlite_type). Names are kept simple and readable.
# TEXT    - strings
# INTEGER - whole numbers
# The "id" column is added automatically as the primary key.
COLUMNS = [
    # Basic info
    ("logged_at",            "TEXT"),     # when this row was recorded
    ("applied_at",           "TEXT"),     # when actually applied (blank if not)
    ("company",              "TEXT"),
    ("job_title",            "TEXT"),
    ("location",             "TEXT"),
    ("job_url",              "TEXT"),
    ("job_id",               "TEXT"),     # stable LinkedIn job ID (for dedup)
    ("status",               "TEXT"),     # applied / skipped / failed / error

    # AI relevance analysis
    ("match_score",          "INTEGER"),
    ("skill_overlap",        "INTEGER"),
    ("matched_skills",       "TEXT"),
    ("missing_skills",       "TEXT"),
    ("domain_match",         "TEXT"),
    ("transferable",         "TEXT"),
    ("specialization_gap",   "TEXT"),
    ("ai_reason",            "TEXT"),
    ("role_equivalent",      "TEXT"),

    # Resume files
    ("docx_path",            "TEXT"),
    ("pdf_path",             "TEXT"),
    ("resume_ready",         "INTEGER"),  # 1 once the tailored resume is generated

    # Phase 1 stores the full job description here, keyed by job_url
    ("job_description",      "TEXT"),
    ("jd_metadata_json",     "TEXT"),     # Stage 2 extracted: skills, seniority, domain, acronyms

    # Re-application review intent (only meaningful for applied jobs):
    #   NULL / empty -> not yet reviewed (a normal applied job)
    #   0            -> skip for now  (ask again next run)
    #   1            -> skip forever  (never ask again)
    ("skip",                 "INTEGER"),

    # Search context
    ("search_role",          "TEXT"),
    ("apply_mode",           "TEXT"),

    # Easy Apply debugging
    ("easy_apply_detected",  "TEXT"),
    ("button_found",         "TEXT"),
    ("button_selector",      "TEXT"),
    ("button_text",          "TEXT"),
    ("modal_opened",         "TEXT"),
    ("modal_selector",       "TEXT"),
    ("steps_completed",      "INTEGER"),
    ("fields_filled",        "INTEGER"),
    ("resume_uploaded",      "TEXT"),
    ("submit_found",         "TEXT"),
    ("submit_selector",      "TEXT"),
    ("submission_confirmed", "TEXT"),
    ("error_messages",       "TEXT"),
    ("failure_step",         "INTEGER"),
    ("failure_reason",       "TEXT"),
    ("screenshot_path",      "TEXT"),

    # Notes
    ("notes",                "TEXT"),
    ("stretch",              "INTEGER"),  # 1 = apply but upskilling recommended
]

# Just the column names, in order
COLUMN_NAMES = [name for name, _ in COLUMNS]


# -- Database setup ----------------------------------------------

def _connect() -> sqlite3.Connection:
    """
    Opens a connection to the database file, creating the folder
    and the table if they do not exist yet.
    """
    os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    # Return rows as dict-like objects so we can access columns by name
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    """
    Creates the applications table if it is not already there, then
    adds any columns that are missing.

    CREATE TABLE IF NOT EXISTS handles a brand-new database. For a
    database created by an older version of this file, we also check
    each expected column and ALTER TABLE in any that are missing -
    so upgrading the schema never loses existing data.
    """
    column_defs = ",\n    ".join(f"{name} {col_type}" for name, col_type in COLUMNS)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {column_defs}
        )
        """
    )

    # Find which columns already exist in the table
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")}

    # Add any expected column that is missing (schema upgrade)
    for name, col_type in COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {col_type}")
            print(f"   [FIX] Added new column to database: {name}")

    # Index job_url and job_id for fast dedup lookups
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_job_url ON {TABLE} (job_url)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_job_id ON {TABLE} (job_id)"
    )

    conn.commit()

    # One-time backfill: fill job_id for any existing rows that have a
    # URL but no extracted ID yet (rows from before the job_id column).
    _backfill_job_ids(conn)


def _backfill_job_ids(conn: sqlite3.Connection) -> None:
    """
    Fills the job_id column for existing rows that don't have it yet.
    Runs harmlessly every startup - once every row has an id (or has
    been checked), it finds nothing to do and returns immediately.
    """
    rows = conn.execute(
        f"""SELECT id, job_url FROM {TABLE}
            WHERE (job_id IS NULL OR job_id = '')
              AND job_url IS NOT NULL AND job_url != ''"""
    ).fetchall()

    if not rows:
        return

    filled = 0
    for r in rows:
        jid = extract_job_id(r["job_url"])
        if jid:
            conn.execute(
                f"UPDATE {TABLE} SET job_id = ? WHERE id = ?",
                (jid, r["id"]),
            )
            filled += 1
    conn.commit()
    if filled:
        print(f"   [FIX] Backfilled job IDs for {filled} existing row(s)")


def init_db() -> None:
    """Public helper - ensures the database and table exist."""
    conn = _connect()
    conn.close()
    print(f"   [DB]  Database ready: {DB_FILE}")


# -- Internal helpers --------------------------------------------

def _to_int(value):
    """Convert a value to int, or return None if it is not numeric."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cleanup_duplicate_rows() -> dict:
    """
    Startup cleanup — removes duplicate rows for the same job_url.

    Deduplication priority (which row to KEEP):
    1. status = 'applied'  over 'matched', 'skipped', 'failed'
    2. Most recent logged_at within same status

    Called once when the app starts. Safe to call multiple times.
    Returns {'removed': N} count of deleted rows.
    """
    if not os.path.exists(DB_FILE):
        return {"removed": 0}

    conn = _connect()
    removed = 0
    try:
        # Find duplicates by job_id first (stable LinkedIn ID),
        # then fall back to job_url for non-LinkedIn jobs.
        # job_url changes tracking params between visits — job_id is stable.

        # Pass 1: duplicates by job_id
        dupes_by_id = conn.execute(f"""
            SELECT job_id, COUNT(*) as cnt
            FROM {TABLE}
            WHERE job_id IS NOT NULL AND job_id != ''
            GROUP BY job_id
            HAVING cnt > 1
        """).fetchall()

        for dupe in dupes_by_id:
            jid = dupe["job_id"]
            rows = conn.execute(f"""
                SELECT id, status, logged_at
                FROM {TABLE}
                WHERE job_id = ?
                ORDER BY
                    CASE status
                        WHEN 'applied' THEN 0
                        WHEN 'matched' THEN 1
                        WHEN 'failed'  THEN 2
                        WHEN 'skipped' THEN 3
                        ELSE 4
                    END ASC,
                    logged_at DESC
            """, (jid,)).fetchall()
            if len(rows) > 1:
                keep_id  = rows[0]["id"]
                drop_ids = [r["id"] for r in rows[1:]]
                conn.execute(
                    f"DELETE FROM {TABLE} WHERE id IN (%s)"
                    % ",".join("?" * len(drop_ids)), drop_ids)
                removed += len(drop_ids)

        # Pass 2: duplicates by job_url (for jobs without job_id)
        dupes = conn.execute(f"""
            SELECT job_url, COUNT(*) as cnt
            FROM {TABLE}
            WHERE (job_id IS NULL OR job_id = '')
              AND job_url IS NOT NULL AND job_url != ''
            GROUP BY job_url
            HAVING cnt > 1
        """).fetchall()

        for dupe in dupes:
            url = dupe["job_url"]
            rows = conn.execute(f"""
                SELECT id, status, logged_at
                FROM {TABLE}
                WHERE job_url = ?
                ORDER BY
                    CASE status
                        WHEN 'applied' THEN 0
                        WHEN 'matched' THEN 1
                        WHEN 'failed'  THEN 2
                        WHEN 'skipped' THEN 3
                        ELSE 4
                    END ASC,
                    logged_at DESC
            """, (url,)).fetchall()

            if len(rows) <= 1:
                continue
            keep_id  = rows[0]["id"]
            drop_ids = [r["id"] for r in rows[1:]]
            conn.execute(
                f"DELETE FROM {TABLE} WHERE id IN (%s)"
                % ",".join("?" * len(drop_ids)), drop_ids)
            removed += len(drop_ids)

        conn.commit()
    finally:
        conn.close()

    if removed:
        print("[DB] Cleaned up %d duplicate job row(s)." % removed)

    return {"removed": removed}


def extract_job_id(url: str) -> str:
    """
    Pulls the stable LinkedIn job ID out of a job URL.

    LinkedIn shows the same job under different URL shapes, e.g.:
        .../jobs/search/?currentJobId=4416255254
        .../jobs/view/4416255254/?refId=...&trackingId=...
    The tracking parameters change between visits, so comparing whole
    URLs misses duplicates. The numeric job ID, however, is stable -
    so we dedup on that instead.

    Returns the ID as a string (e.g. "4416255254"), or "" if no ID
    can be found (e.g. an external non-LinkedIn job URL).
    """
    if not url:
        return ""

    # Shape 1: currentJobId= or jobId= as a query parameter
    m = re.search(r"(?:currentJobId|jobId)=(\d+)", url)
    if m:
        return m.group(1)

    # Shape 2: /jobs/view/<digits> in the path
    m = re.search(r"/jobs/view/(\d+)", url)
    if m:
        return m.group(1)

    # No recognizable LinkedIn job ID (e.g. an external job site)
    return ""


def _insert_row(conn: sqlite3.Connection, row: dict) -> None:
    """
    Inserts one row. Uses ? placeholders (never string formatting)
    so the values are passed safely - this avoids SQL injection and
    handles quotes/odd characters in company names correctly.
    """
    placeholders = ", ".join("?" for _ in COLUMN_NAMES)
    columns      = ", ".join(COLUMN_NAMES)
    values       = [row.get(name) for name in COLUMN_NAMES]
    conn.execute(
        f"INSERT INTO {TABLE} ({columns}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


# -- Public: log an application ----------------------------------

def log_application(
    job:         dict,
    status:      str,
    docx_path:   str = "",
    pdf_path:    str = "",
    relevance:   dict = None,
    search_role: str = "",
    apply_mode:  str = "",
    debug_info:  dict = None,
    notes:       str = "",
) -> None:
    """
    Records one job application in the database.

    Same signature as the old CSV version, so callers do not change.
    """
    rel = relevance or {}
    dbg = debug_info or {}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = {
        # Basic info
        "logged_at":            now,
        "applied_at":           now if status == "applied" else "",
        "company":              job.get("company", ""),
        "job_title":            job.get("title", ""),
        "location":             job.get("location", ""),
        "job_url":              job.get("url", ""),
        "job_id":               extract_job_id(job.get("url", "")),
        "status":               status,

        # AI relevance analysis
        "match_score":          _to_int(rel.get("match_score")),
        "skill_overlap":        _to_int(rel.get("skill_overlap")),
        "matched_skills":       " | ".join(rel.get("matched_skills", [])),
        "missing_skills":       " | ".join(rel.get("missing_skills", [])),
        "domain_match":         str(rel.get("domain_match", "")),
        "transferable":         str(rel.get("transferable", "")),
        "specialization_gap":   str(rel.get("specialization_gap", "")),
        "ai_reason":            rel.get("reason", ""),
        "role_equivalent":      rel.get("role_equivalent", ""),

        # Resume files
        "docx_path":            docx_path,
        "pdf_path":             pdf_path,

        # Search context
        "search_role":          search_role,
        "apply_mode":           apply_mode,

        # Easy Apply debugging
        "easy_apply_detected":  str(dbg.get("easy_apply_detected", "")),
        "button_found":         str(dbg.get("easy_apply_button_found", "")),
        "button_selector":      dbg.get("button_selector_used", ""),
        "button_text":          dbg.get("button_text", ""),
        "modal_opened":         str(dbg.get("modal_opened", "")),
        "modal_selector":       dbg.get("modal_selector_used", ""),
        "steps_completed":      _to_int(dbg.get("steps_completed")),
        "fields_filled":        _to_int(dbg.get("fields_filled")),
        "resume_uploaded":      str(dbg.get("resume_uploaded", "")),
        "submit_found":         str(dbg.get("submit_button_found", "")),
        "submit_selector":      dbg.get("submit_selector_used", ""),
        "submission_confirmed": str(dbg.get("submission_confirmed", "")),
        "error_messages":       dbg.get("error_messages", ""),
        "failure_step":         _to_int(dbg.get("failure_step")),
        "failure_reason":       dbg.get("failure_reason", ""),
        "screenshot_path":      dbg.get("screenshot_path", ""),

        # Notes
        "notes":                notes,
    }

    conn = _connect()
    try:
        url = job.get("url", "").strip()
        jid = extract_job_id(url)

        # If this job already has any row, update instead of inserting a duplicate.
        # Use job_id as primary key (stable) — fall back to job_url.
        if status == "applied" and (jid or url):
            existing = None
            if jid:
                existing = conn.execute(
                    f"SELECT id FROM {TABLE} WHERE job_id = ? AND status = 'applied' LIMIT 1",
                    (jid,)
                ).fetchone()
            if not existing and url:
                existing = conn.execute(
                    f"SELECT id FROM {TABLE} WHERE job_url = ? AND status = 'applied' LIMIT 1",
                    (url,)
                ).fetchone()
            if existing:
                conn.execute(
                    f"""UPDATE {TABLE}
                        SET applied_at = ?, logged_at = ?, notes = ?
                        WHERE id = ?""",
                    (row["applied_at"], row["logged_at"],
                     (notes + " [Re-applied]").strip(), existing["id"])
                )
                conn.commit()
                print("   [DB] Updated re-apply: [OK] %s -- applied"
                      % job.get("company"))
                return

        _insert_row(conn, row)
    finally:
        conn.close()

    icon = {"applied": "[OK]", "failed": "[ERR]", "skipped": "[SKIP] ", "error": "[WARN] "}.get(status, "*")
    print(f"   [DB] Logged: {icon} {job.get('company')} -- {status}")
    if pdf_path:
        print(f"   [FILE] Resume: {pdf_path}")
    if dbg.get("screenshot_path"):
        print(f"   [IMG] Screenshot: {dbg.get('screenshot_path')}")


# -- Public: dedup lookups ---------------------------------------

def load_seen_urls() -> set:
    """
    Returns the set of identity keys for every job ever logged -
    applied, skipped, failed, or errored. Seeding the scraper with
    this skips jobs already processed in previous runs.

    Each job contributes BOTH its job_id and its full job_url to the
    set, so a job matches whether it is recognised by stable ID
    (LinkedIn jobs) or by URL (external jobs with no ID).
    """
    seen = set()
    if not os.path.exists(DB_FILE):
        return seen
    conn = _connect()
    try:
        for row in conn.execute(
                f"SELECT job_url, job_id FROM {TABLE} "                f"WHERE status != 'scanning'"):
            url = (row["job_url"] or "").strip()
            jid = (row["job_id"] or "").strip()
            if url:
                seen.add(url)
            if jid:
                seen.add(jid)
    finally:
        conn.close()
    return seen


def load_applied_urls() -> set:
    """
    Returns the identity keys of jobs actually applied to
    (status = 'applied').

    As with load_seen_urls, each applied job contributes BOTH its
    job_id and its job_url, so dedup is robust to LinkedIn's
    changing URL tracking parameters.
    """
    applied = set()
    if not os.path.exists(DB_FILE):
        return applied
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT job_url, job_id FROM {TABLE} WHERE status = ?",
            ("applied",),
        )
        for row in rows:
            url = (row["job_url"] or "").strip()
            jid = (row["job_id"] or "").strip()
            if url:
                applied.add(url)
            if jid:
                applied.add(jid)
    finally:
        conn.close()
    return applied



def clear_all_history() -> int:
    """
    Delete ALL rows from the applications table.
    Called from the Clear All History button in the History tab.
    Returns the number of rows deleted.
    """
    if not os.path.exists(DB_FILE):
        return 0
    conn = _connect()
    try:
        cur = conn.execute(f"DELETE FROM {TABLE}")
        conn.commit()
        deleted = cur.rowcount
        log("History cleared: %d records deleted" % deleted)
        return deleted
    finally:
        conn.close()


def job_identity_keys(url: str) -> set:
    """
    Returns the set of identity keys for a job URL - its extracted
    job_id (if any) and the URL itself. A job is a duplicate if ANY
    of its keys is in the applied/seen set.
    """
    keys = set()
    url = (url or "").strip()
    if url:
        keys.add(url)
    jid = extract_job_id(url)
    if jid:
        keys.add(jid)
    return keys


# -- Public: re-application review helpers -----------------------

def _days_since(date_str: str) -> int:
    """
    Returns how many whole days ago the given date was, counting
    from today. Returns -1 if the date cannot be parsed.
    """
    if not date_str:
        return -1
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return (datetime.now() - dt).days
        except ValueError:
            continue
    return -1


def is_already_applied(job_url: str) -> bool:
    """
    True if this job has any row with status 'applied'.
    Matches on the stable job ID when available, falling back to the
    full URL - so LinkedIn's changing tracking params don't cause a
    duplicate to be missed.
    """
    url = (job_url or "").strip()
    if not url or not os.path.exists(DB_FILE):
        return False
    jid = extract_job_id(url)
    conn = _connect()
    try:
        if jid:
            row = conn.execute(
                f"""SELECT 1 FROM {TABLE}
                    WHERE status = 'applied' AND (job_id = ? OR job_url = ?)
                    LIMIT 1""",
                (jid, url),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT 1 FROM {TABLE} WHERE job_url = ? AND status = 'applied' LIMIT 1",
                (url,),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_reapply_candidates(session_start: str = None) -> list:
    """
    Returns previously-applied jobs for end-of-run re-application review.

    EXCLUDES jobs applied in the current session — those were just
    applied and should not be offered for immediate re-review.
    Only shows jobs applied in PREVIOUS runs (at least 1 hour ago).

    Args:
        session_start: ISO timestamp when the current bot session started.
                       Jobs applied after this time are excluded.
                       If None, excludes jobs applied in the last hour.

    Each item is a dict with company, job_title, job_url, applied_at,
    and days_ago (whole days since applied_at).
    """
    if not os.path.exists(DB_FILE):
        return []

    from datetime import datetime, timedelta
    if session_start:
        cutoff = session_start
    else:
        # Exclude anything applied in the last hour
        cutoff = (datetime.now() - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT job_url, company, job_title, applied_at, skip
            FROM {TABLE}
            WHERE status = 'applied'
              AND (skip IS NULL OR skip = 0)
              AND (applied_at IS NULL OR applied_at < ?)
            ORDER BY applied_at DESC
            """,
            (cutoff,)
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "company":    r["company"]   or "Unknown company",
            "job_title":  r["job_title"] or "Unknown role",
            "job_url":    r["job_url"]   or "",
            "applied_at": r["applied_at"] or "",
            "days_ago":   _days_since(r["applied_at"]),
        }
        for r in rows
        if (r["job_url"] or "").strip()
    ]


def set_skip_status(job_url: str, skip_value: int) -> None:
    """
    Records a re-application review decision for a job URL.
        skip_value = 0  -> skip for now  (ask again next run)
        skip_value = 1  -> skip forever  (never ask again)
    Applies to all applied rows for that URL.
    """
    url = (job_url or "").strip()
    if not url or not os.path.exists(DB_FILE):
        return
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE {TABLE} SET skip = ? WHERE job_url = ? AND status = 'applied'",
            (skip_value, url),
        )
        conn.commit()
    finally:
        conn.close()


# -- Public: phased-flow helpers (scan / generate / apply) -------

def save_matched_job(job: dict, relevance: dict, search_role: str = "",
                     apply_mode: str = "") -> None:
    """
    Phase 1: stores a job that passed the relevance checks, including
    its full description, so resumes can be generated later (Phase 2)
    and the job applied to later (Phase 3).

    If a row for this job_url already exists with status 'matched',
    it is updated rather than duplicated.
    """
    url = (job.get("url") or "").strip()
    rel = relevance or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        existing = conn.execute(
            f"SELECT id FROM {TABLE} WHERE job_url = ? AND status = 'matched' LIMIT 1",
            (url,),
        ).fetchone()

        if existing:
            conn.execute(
                f"""UPDATE {TABLE}
                    SET job_description = ?, match_score = ?, skill_overlap = ?,
                        ai_reason = ?, logged_at = ?
                    WHERE id = ?""",
                (job.get("description", ""), _to_int(rel.get("match_score")),
                 _to_int(rel.get("skill_overlap")), rel.get("reason", ""),
                 now, existing["id"]),
            )
        else:
            row = {name: None for name in COLUMN_NAMES}
            row.update({
                "logged_at":          now,
                "company":            job.get("company", ""),
                "job_title":          job.get("title", ""),
                "location":           job.get("location", ""),
                "job_url":            url,
                "job_id":             extract_job_id(url),
                "status":             "matched",
                "job_description":    job.get("description", ""),
                "match_score":        _to_int(rel.get("match_score")),
                "skill_overlap":      _to_int(rel.get("skill_overlap")),
                "matched_skills":     " | ".join(rel.get("matched_skills", [])),
                "missing_skills":     " | ".join(rel.get("missing_skills", [])),
                "domain_match":       str(rel.get("domain_match", "")),
                "specialization_gap": str(rel.get("specialization_gap", "")),
                "ai_reason":          rel.get("reason", ""),
                "role_equivalent":    rel.get("role_equivalent", ""),
                "search_role":        search_role,
                "apply_mode":         apply_mode,
                "resume_ready":        0,
                "stretch":            1 if rel.get("stretch") else 0,
            })
            _insert_row(conn, row)
        conn.commit()
    finally:
        conn.close()



def load_applied_urls() -> set:
    """
    Returns identity keys for ONLY confirmed applied jobs.
    Used by orchestrator to permanently skip re-application.
    Does NOT include skipped/pre-filtered — those are re-evaluated
    each run via LinkedIn DOM scraping.
    """
    seen = set()
    if not os.path.exists(DB_FILE):
        return seen
    conn = _connect()
    try:
        for row in conn.execute(
            f"""SELECT job_url, job_id FROM {TABLE}
                WHERE status IN ('applied', 'resume_ready', 'matched')"""
        ):
            url = (row["job_url"] or "").strip()
            jid = (row["job_id"] or "").strip()
            if url: seen.add(url)
            if jid: seen.add(jid)
        return seen
    finally:
        conn.close()


def get_jobs_needing_resume(session_start: str = None,
                                job_ids: list = None) -> list:
    """
    Phase 2: returns matched jobs that do not yet have a resume.

    session_start: ISO datetime string — only return jobs from this session.
                   If None, returns ALL unprocessed matched jobs (old behaviour).
    job_ids:       explicit list of row IDs to process (manual selection).
                   Overrides session_start filter.
    """
    if not os.path.exists(DB_FILE):
        return []
    conn = _connect()
    try:
        if job_ids:
            # Manual selection — specific jobs regardless of session
            placeholders = ",".join("?" * len(job_ids))
            rows = conn.execute(
                f"""SELECT id, company, job_title, location, job_url, job_description, jd_metadata_json
                    FROM {TABLE}
                    WHERE id IN ({placeholders})
                      AND status IN ('matched','resume_ready','applied')""",
                job_ids
            ).fetchall()
        elif session_start:
            # Current session only — avoids replaying old failures
            rows = conn.execute(
                f"""SELECT id, company, job_title, location, job_url, job_description, jd_metadata_json
                    FROM {TABLE}
                    WHERE status = 'matched'
                      AND (resume_ready IS NULL OR resume_ready = 0)
                      AND logged_at >= ?""",
                (session_start,)
            ).fetchall()
        else:
            # Fallback: all unprocessed (original behaviour)
            rows = conn.execute(
                f"""SELECT id, company, job_title, location, job_url, job_description, jd_metadata_json
                    FROM {TABLE}
                    WHERE status = 'matched'
                      AND (resume_ready IS NULL OR resume_ready = 0)"""
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_scanning_job(job: dict) -> int:
    """
    Write job to DB immediately when scraper finds it — before relevance check.
    Returns the row id so orchestrator can update it later.
    Returns -1 on any failure so caller always gets a valid int.
    Status = 'scanning' means currently being analyzed by Claude.
    """
    try:
        conn = _connect()
    except Exception as e:
        _log("save_scanning_job: DB connect failed: %s" % e)
        return -1
    try:
        url    = job.get("url", "").strip()
        job_id = extract_job_id(url)
        now    = _now()

        # Check if already exists (same job_id or url)
        existing = None
        if job_id:
            existing = conn.execute(
                f"SELECT id FROM {TABLE} WHERE job_id = ? LIMIT 1",
                (job_id,)).fetchone()
        if not existing and url:
            existing = conn.execute(
                f"SELECT id FROM {TABLE} WHERE job_url = ? LIMIT 1",
                (url,)).fetchone()

        if existing:
            # Update existing row to scanning
            conn.execute(
                f"UPDATE {TABLE} SET status='scanning', logged_at=? WHERE id=?",
                (now, existing["id"]))
            conn.commit()
            return existing["id"]

        # Insert new scanning row including description for Phase 2
        conn.execute(
            f"""INSERT INTO {TABLE}
                (job_url, job_id, job_title, company, location,
                 status, logged_at, search_role, job_description)
                VALUES (?,?,?,?,?,'scanning',?,?,?)""",
            (url, job_id,
             job.get("title",""), job.get("company",""),
             job.get("location",""), now,
             job.get("search_role",""),
             job.get("description",""))
        )
        conn.commit()
        row = conn.execute(
            f"SELECT id FROM {TABLE} WHERE job_url=? ORDER BY id DESC LIMIT 1",
            (url,)).fetchone()
        return row["id"] if row else -1
    except Exception as e:
        _log("save_scanning_job error: %s" % e)
        try: conn.close()
        except Exception: pass
        return -1
    finally:
        try: conn.close()
        except Exception: pass


def update_job_relevance(row_id: int, job: dict,
                          relevance: dict, search_role: str = "",
                          apply_mode: str = "") -> None:
    """
    Update a scanning row with Claude's relevance result.
    Sets status to 'matched' or 'skipped' based on is_relevant.
    """
    if row_id < 0:
        return
    conn = _connect()
    try:
        status = "matched" if relevance.get("is_relevant") else "skipped"
        notes  = relevance.get("reason", "")
        stretch = 1 if relevance.get("stretch") else 0
        # Save job description and Stage 2 JD metadata for Phase 2 resume generation
        desc     = job.get("description","") or job.get("job_description","")
        jd_meta  = job.get("_jd_metadata") or {}
        jd_json  = json.dumps(jd_meta) if jd_meta else None
        conn.execute(
            f"""UPDATE {TABLE} SET
                status=?, match_score=?, skill_overlap=?,
                ai_reason=?, notes=?, search_role=?,
                apply_mode=?, stretch=?, logged_at=?,
                job_description=?, jd_metadata_json=?
                WHERE id=?""",
            (status,
             relevance.get("match_score", 0),
             relevance.get("skill_overlap", 0),
             relevance.get("reason","")[:500],
             notes[:300], search_role, apply_mode,
             stretch, _now(),
             desc or None, jd_json, row_id)
        )
        conn.commit()
    finally:
        conn.close()


def mark_resume_ready(row_id: int, docx_path: str, pdf_path: str) -> None:
    """Phase 2: records that a job's tailored resume has been generated."""
    conn = _connect()
    try:
        conn.execute(
            f"""UPDATE {TABLE}
                SET resume_ready = 1, docx_path = ?, pdf_path = ?
                WHERE id = ?""",
            (docx_path, pdf_path, row_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_jobs_ready_to_apply() -> list:
    """
    Phase 3: returns matched jobs whose resume is ready, so the bot
    can open each one for the user to apply.
    """
    if not os.path.exists(DB_FILE):
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            f"""SELECT id, company, job_title, location, job_url,
                       docx_path, pdf_path,
                       match_score, skill_overlap, ai_reason,
                       matched_skills, missing_skills
                FROM {TABLE}
                WHERE status = 'matched' AND resume_ready = 1"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_job_outcome(row_id: int, status: str) -> None:
    """
    Phase 3: updates a matched job's row with the final outcome once
    the user has acted on it ('applied', 'skipped', etc.).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    applied_at = now if status == "applied" else ""
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE {TABLE} SET status = ?, applied_at = ? WHERE id = ?",
            (status, applied_at, row_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_jobs(row_ids: list) -> dict:
    """
    Delete specific jobs by row id.
    Also removes the associated resume files (docx + pdf).
    If the parent folder becomes empty after deletion, removes it too.
    Returns {"rows": N, "files": N}.
    """
    if not row_ids or not os.path.exists(DB_FILE):
        return {"rows": 0, "files": 0}

    files_removed = 0
    conn = _connect()
    try:
        placeholders = ",".join("?" * len(row_ids))
        rows = conn.execute(
            f"SELECT docx_path, pdf_path FROM {TABLE} WHERE id IN ({placeholders})",
            row_ids,
        ).fetchall()

        for r in rows:
            for path in (r["docx_path"], r["pdf_path"]):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        files_removed += 1
                        # Remove parent folder if now empty
                        folder = os.path.dirname(path)
                        if folder and os.path.isdir(folder):
                            if not os.listdir(folder):
                                os.rmdir(folder)
                    except Exception:
                        pass

        conn.execute(
            f"DELETE FROM {TABLE} WHERE id IN ({placeholders})", row_ids)
        conn.commit()
    finally:
        conn.close()

    return {"rows": len(row_ids), "files": files_removed}


def clear_unfinished_run() -> dict:
    """
    Removes 'matched' jobs that were never applied to - the leftovers
    of an interrupted or failed run (e.g. a run with a bad API key).

    Order matters: the resume files are deleted FIRST (while the rows
    still tell us their paths), then the database rows. Deleting the
    rows first would orphan the files with nothing pointing to them.

    Only files recorded on the unfinished 'matched' rows are removed -
    resumes tied to real applied jobs are never touched.

    Returns {'rows': n, 'files': n}.
    """
    if not os.path.exists(DB_FILE):
        return {"rows": 0, "files": 0}

    conn = _connect()
    try:
        # 1. Collect the matched rows and their file paths
        rows = conn.execute(
            f"SELECT docx_path, pdf_path FROM {TABLE} WHERE status = 'matched'"
        ).fetchall()

        # 2. Delete the resume files FIRST
        files_removed = 0
        for r in rows:
            for path in (r["docx_path"], r["pdf_path"]):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        files_removed += 1
                        print(f"   [DEL]  Deleted file: {path}")
                    except Exception as e:
                        print(f"   [WARN]  Could not delete {path}: {e}")

        # 3. Now delete the database rows
        cur = conn.execute(f"DELETE FROM {TABLE} WHERE status = 'matched'")
        rows_removed = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    return {"rows": rows_removed, "files": files_removed}


def has_unfinished_run() -> int:
    """
    Returns how many 'matched' (not-yet-applied) jobs are sitting in
    the database from a previous run. 0 means the last run finished
    cleanly.
    """
    if not os.path.exists(DB_FILE):
        return 0
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {TABLE} WHERE status = 'matched'"
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


# -- Public: statistics ------------------------------------------

def show_stats() -> None:
    """Prints a summary of all tracked applications using SQL aggregates."""
    if not os.path.exists(DB_FILE):
        print("No applications tracked yet.")
        return

    conn = _connect()
    try:
        counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                f"SELECT status, COUNT(*) AS n FROM {TABLE} GROUP BY status"
            )
        }
        total = sum(counts.values())
        if not total:
            print("No applications tracked yet.")
            return

        avg_row = conn.execute(
            f"SELECT AVG(match_score) AS avg FROM {TABLE} "
            f"WHERE status = 'applied' AND match_score IS NOT NULL"
        ).fetchone()
        avg_score = int(avg_row["avg"]) if avg_row and avg_row["avg"] else 0

        print(f"\n{'='*60}")
        print(f"[DB] Application Summary")
        print(f"{'='*60}")
        print(f"   Total Processed   : {total}")
        print(f"   [OK]   Applied    : {counts.get('applied', 0)}")
        failed = counts.get('failed', 0)
        if failed:
            print(f"   [ERR]  Failed     : {failed}")
        else:
            print(f"   [OK]   Failed     : 0")
        skipped_n = counts.get('skipped', 0)
        print(f"   [SKIP] Skipped    : {skipped_n}")
        errors_n = counts.get('error', 0)
        if errors_n:
            print(f"   [ERR]  Errors     : {errors_n}")
        print(f"   [OK]   Avg Match  : {avg_score}/100")
        print(f"{'='*60}")

        applied = conn.execute(
            f"""SELECT job_title, company, applied_at, logged_at,
                       match_score, skill_overlap, pdf_path
                FROM {TABLE} WHERE status = 'applied'
                ORDER BY applied_at DESC LIMIT 10"""
        ).fetchall()

        if applied:
            print(f"\n[OK] Successfully Applied Jobs:")
            for r in applied:
                line = f"   * {r['job_title'] or 'Unknown role'} @ {r['company'] or 'Unknown company'}"
                when = r["applied_at"] or r["logged_at"] or ""
                if when:
                    line += f"   (applied {when})"
                print(line)
                if r["match_score"] is not None:
                    print(f"     Score: {r['match_score']}/100  |  Skills: {r['skill_overlap'] or 'N/A'}%")
                if r["pdf_path"]:
                    print(f"     PDF: {r['pdf_path']}")
            total_applied = counts.get("applied", 0)
            if total_applied > 10:
                print(f"   ... and {total_applied - 10} more")

        skipped = conn.execute(
            f"""SELECT job_title, company, ai_reason, failure_reason
                FROM {TABLE} WHERE status = 'skipped' LIMIT 5"""
        ).fetchall()

        if skipped:
            print(f"\n[SKIP]  Skipped Jobs (Top Reasons):")
            for r in skipped:
                print(f"   * {r['job_title'] or 'Unknown'} @ {r['company'] or 'Unknown'}")
                print(f"     Reason: {r['ai_reason'] or r['failure_reason'] or 'No reason recorded'}")

    finally:
        conn.close()

    print(f"\n[DB]  Database: {DB_FILE}")
    print(f"   Run export_to_csv() to get a spreadsheet you can open in Excel.")


def export_to_csv(csv_path: str = None) -> str:
    """
    Writes the entire applications table out to a CSV file so it can
    be opened in Excel. The database stays the source of truth; this
    CSV is a disposable export you can generate any time.

    Returns the path of the CSV that was written.
    """
    if csv_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(_OUTPUT_DIR, f"applications_export_{stamp}.csv")

    if not os.path.exists(DB_FILE):
        print("No database to export yet.")
        return ""

    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {TABLE} ORDER BY id")]
    finally:
        conn.close()

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    # "id" plus all the data columns
    fieldnames = ["id"] + COLUMN_NAMES
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"   [OUT] Exported {len(rows)} rows -> {csv_path}")
    return csv_path


# -- Run directly for a quick check ------------------------------

if __name__ == "__main__":
    init_db()
    show_stats()