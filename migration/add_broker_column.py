"""
migration/add_broker_column.py
==============================
Add the `broker` column to the tables that track trade activity:
  singles, strangles, calendars

Run once against an existing optionsStrat.db that was created before
broker logging was introduced.  Safe to re-run — it skips tables that
already have the column.

Usage
-----
    python -m migration.add_broker_column
"""

import sqlite3
from models.base import _DB_PATH


_TABLES = [
    "singles",
    "strangles",
    "calendars",
]


def _has_column(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def run(db_path: str = _DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    for table in _TABLES:
        if _has_column(cur, table, "broker"):
            print(f"  {table}: broker column already exists — skipped")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN broker TEXT")
            print(f"  {table}: broker column added")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    run()
