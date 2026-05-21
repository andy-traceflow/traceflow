"""Apply all SQL migrations in `migrations/` in lexical (numeric-prefix) order.

Tracks applied migrations in a `schema_migrations` table for idempotency.
Re-running this script after the initial bootstrap is safe — already-applied
migrations are skipped.

Each migration file is responsible for its own transaction (BEGIN/COMMIT
inside the .sql file). If a migration fails mid-flight, PostgreSQL rolls
back its DDL and this script exits non-zero before recording the migration
as applied — so no partial state lands in `schema_migrations`.

Usage (bash):
    SUPABASE_DB_URL='postgresql://postgres:PASSWORD@db.<ref>.supabase.co:5432/postgres' \
        python scripts/apply_migrations.py

Usage (PowerShell):
    $env:SUPABASE_DB_URL = 'postgresql://...'
    python scripts/apply_migrations.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print(
            "ERROR: SUPABASE_DB_URL environment variable is required",
            file=sys.stderr,
        )
        return 1

    conn = await asyncpg.connect(dsn)
    try:
        # Tracking table — created outside any user-authored migration so it
        # exists before the first one runs. Idempotent.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name        TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        applied = {
            row["name"]
            for row in await conn.fetch("SELECT name FROM schema_migrations")
        }

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            print(f"No .sql files found in {MIGRATIONS_DIR}")
            return 0

        new_count = 0
        for path in migration_files:
            name = path.name
            if name in applied:
                print(f"  SKIP  {name}")
                continue

            sql = path.read_text(encoding="utf-8")
            print(f"  APPLY {name} ...")
            try:
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES ($1)",
                    name,
                )
                print("        OK")
                new_count += 1
            except Exception as e:
                print(f"        FAILED: {e}", file=sys.stderr)
                return 1

        print(
            f"\nDone. {new_count} new migration(s) applied, "
            f"{len(applied)} previously applied."
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
