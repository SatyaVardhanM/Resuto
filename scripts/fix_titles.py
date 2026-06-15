"""
One-time fix: clean double job titles already stored in the database.

LinkedIn's job card elements return two lines via inner_text():
  Line 1 — the real title
  Line 2 — "<title> with verification"

The scraper was saving both lines joined with \n. This script
strips line 2 from every affected row.

Run once:   python fix_titles.py
"""

import sqlite3, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from core.settings import get_output_dir

db = os.path.join(get_output_dir(), "applications.db")
if not os.path.exists(db):
    print("Database not found -- nothing to fix.")
    sys.exit(0)

with sqlite3.connect(db) as conn:
    conn.execute("PRAGMA journal_mode=WAL")
    rows = conn.execute(
        "SELECT rowid, job_title FROM applications WHERE job_title LIKE '%\n%'"
    ).fetchall()

    if not rows:
        print("No double-title rows found -- database is already clean.")
        sys.exit(0)

    print(f"Found {len(rows)} rows with double titles. Fixing...")
    fixed = 0
    for rowid, title in rows:
        clean = title.splitlines()[0].strip()
        if clean and clean != title:
            conn.execute(
                "UPDATE applications SET job_title=? WHERE rowid=?",
                (clean, rowid))
            print(f"  Fixed: {repr(title[:60])} -> {repr(clean[:40])}")
            fixed += 1

    conn.commit()
    print(f"\nFixed {fixed} row(s). Database cleaned.")
