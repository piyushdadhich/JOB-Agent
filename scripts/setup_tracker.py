"""Initialize a fresh tracker SQLite database from data/schema.sql.

Run from project root with venv active:
    python scripts\\setup_tracker.py
"""

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    project_root = os.getenv("PROJECT_ROOT")
    if not project_root:
        print("ERROR: PROJECT_ROOT not set in .env")
        return 1

    project_root = Path(project_root)
    schema_path = project_root / "data" / "schema.sql"
    db_path = project_root / "data" / "tracker.db"

    if not schema_path.exists():
        print(f"ERROR: schema file not found at {schema_path}")
        return 1

    if db_path.exists():
        print(f"WARNING: database already exists at {db_path}")
        print("Continuing will DROP AND RECREATE the database. "
              "All existing data will be lost.")
        resp = input("Type 'yes' to continue, anything else to abort: ").strip()
        if resp.lower() != "yes":
            print("Aborted.")
            return 1
        db_path.unlink()
        print(f"Removed existing {db_path}")

    schema_sql = schema_path.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(schema_sql)
        conn.commit()
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        version_row = conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        version = version_row[0] if version_row else None
    except sqlite3.Error as e:
        print(f"ERROR: failed to execute schema: {e}")
        return 1
    finally:
        conn.close()

    print(f"Database created at: {db_path}")
    print(f"Schema version: {version}")
    print(f"Tables created ({len(tables)}):")
    for t in tables:
        print(f"  - {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
