"""
Run once after updating api/prompts.py to push the new intake
prompts into the existing prompts.db without losing other prompts.

Usage (from project root):
    python scripts/update_prompts.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.prompts import (
    DB_FILE, _GENERIC_INTAKE_QUESTIONS, _GENERIC_INTAKE_XML,
    PROMPT_INTAKE_QUESTIONS, PROMPT_INTAKE_XML,
)
import sqlite3

conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA journal_mode=WAL")

for name, content in [
    (PROMPT_INTAKE_QUESTIONS, _GENERIC_INTAKE_QUESTIONS),
    (PROMPT_INTAKE_XML,       _GENERIC_INTAKE_XML),
]:
    existing = conn.execute(
        "SELECT id FROM prompts WHERE name=?", (name,)).fetchone()
    if existing:
        conn.execute("UPDATE prompts SET content=? WHERE name=?",
                     (content, name))
        print(f"  Updated : {name}")
    else:
        conn.execute("INSERT INTO prompts (name, content) VALUES (?,?)",
                     (name, content))
        print(f"  Inserted: {name}")

conn.commit()
conn.close()
print("Done. Re-run the intake in Settings to use the new questions.")