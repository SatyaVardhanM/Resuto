# migrate_csv_to_db.py
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
ONE-TIME migration script.

Imports the rows from an existing tracker.csv into the new SQLite
database (applications.db). Run this once, after switching to the
database-backed tracker.py.

Usage:
    python migrate_csv_to_db.py
    python migrate_csv_to_db.py path/to/some_other.csv

The old CSV had different column names than the new database. This
script maps the old names to the new ones automatically. Old columns
that no longer exist are ignored; new columns with no old value are
left blank.

After a successful migration you can archive or delete the old CSV;
the database becomes your single source of truth.
"""
import os
import sys
import csv

import db.tracker as tracker  # the new SQLite-backed tracker


# -- Map OLD csv column names -> NEW database column names -------
# Anything not listed here is matched by identical name if possible.
OLD_TO_NEW = {
    "timestamp":               "logged_at",
    "applied_timestamp":       "applied_at",
    "title":                   "job_title",
    "url":                     "job_url",
    "easy_apply_button_found": "button_found",
    "button_selector_used":    "button_selector",
    "modal_selector_used":     "modal_selector",
    "submit_button_found":     "submit_found",
    "submit_selector_used":    "submit_selector",
    # These already match by name (kept here for clarity):
    # company, location, status, match_score, skill_overlap,
    # matched_skills, missing_skills, domain_match, transferable,
    # ai_reason, role_equivalent, docx_path, pdf_path, search_role,
    # apply_mode, easy_apply_detected, button_text, modal_opened,
    # steps_completed, fields_filled, resume_uploaded,
    # submission_confirmed, error_messages, failure_step,
    # failure_reason, screenshot_path, notes
}

# Integer columns - empty strings must become None, not ""
INT_COLUMNS = {
    "match_score", "skill_overlap", "steps_completed",
    "fields_filled", "failure_step",
}


def _default_csv_path() -> str:
    """Finds the old tracker.csv using local_settings if available."""
    try:
        from core.settings import get_output_dir
        return os.path.join(get_output_dir(), "tracker.csv")
    except Exception:
        return os.path.join("output", "tracker.csv")


def _clean_value(new_col: str, value):
    """Normalises a CSV string value for the database."""
    if value is None:
        return None
    value = str(value).strip()
    if new_col in INT_COLUMNS:
        # Integer columns: blank -> None, otherwise try to convert
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return value


def migrate(csv_path: str) -> None:
    if not os.path.exists(csv_path):
        print(f"[ERR] CSV file not found: {csv_path}")
        print("   Nothing to migrate. Check the path and try again.")
        return

    # Make sure the database and table exist
    tracker.init_db()

    valid_columns = set(tracker.COLUMN_NAMES)
    imported = 0
    skipped  = 0

    conn = tracker._connect()
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                print(f"[ERR] The CSV appears to be empty: {csv_path}")
                return

            for old_row in reader:
                new_row = {}
                for old_col, raw_value in old_row.items():
                    if old_col is None:
                        continue
                    # Translate the column name
                    new_col = OLD_TO_NEW.get(old_col, old_col)
                    # Only keep columns the database actually has
                    if new_col in valid_columns:
                        new_row[new_col] = _clean_value(new_col, raw_value)

                # A row with no usable data is skipped
                if not any(v not in (None, "") for v in new_row.values()):
                    skipped += 1
                    continue

                tracker._insert_row(conn, new_row)
                imported += 1
    finally:
        conn.close()

    print()
    print(f"[OK] Migration complete.")
    print(f"   Imported : {imported} rows")
    if skipped:
        print(f"   Skipped  : {skipped} empty rows")
    print(f"   Database : {tracker.DB_FILE}")
    print()
    print(f"   Your old CSV ({csv_path}) was NOT modified.")
    print(f"   Once you have confirmed the data looks right, you can")
    print(f"   archive or delete it - the database is now the source.")


if __name__ == "__main__":
    # Allow an optional custom CSV path as a command-line argument
    path = sys.argv[1] if len(sys.argv) > 1 else _default_csv_path()
    print(f"[IN] Migrating CSV -> database")
    print(f"   Source: {path}")
    print()
    migrate(path)
