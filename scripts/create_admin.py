"""Seed (or reset) an admin_users account for the /api/admin surface.

Solves the chicken-and-egg: the admin UI needs a login, and the UI never
writes admin_users. Run against any environment by pointing SUPABASE_DB_URL
at it.

    python scripts/create_admin.py --email andy@traceflow.app --name "Andy"

Prompts for the password twice (never echoed, never an argv — argv leaks
into shell history and process lists). Re-running with an existing email
UPDATES the password hash and re-activates the account — this is the
password-reset / un-disable path; there is deliberately no separate tool.

Passwords are bcrypt-hashed via app.services.admin_auth.hash_password (the
single hashing source of truth). bcrypt truncates at 72 bytes, so length is
enforced here: 8–72 bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

import asyncpg

from app.services.admin_auth import hash_password

MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 72  # bcrypt silently truncates beyond this — refuse instead


def _read_password() -> str | None:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("ERROR: passwords do not match", file=sys.stderr)
        return None
    n = len(password.encode())
    if not MIN_PASSWORD_BYTES <= n <= MAX_PASSWORD_BYTES:
        print(
            f"ERROR: password must be {MIN_PASSWORD_BYTES}-{MAX_PASSWORD_BYTES} bytes "
            f"(got {n})",
            file=sys.stderr,
        )
        return None
    return password


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create or reset a TraceFlow admin account")
    parser.add_argument("--email", required=True, help="Login email (stored lowercased)")
    parser.add_argument("--name", default="", help="Display name")
    parser.add_argument("--role", default="owner", help="Role (only 'owner' until RBAC lands)")
    args = parser.parse_args()

    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: SUPABASE_DB_URL environment variable is required", file=sys.stderr)
        return 1

    password = _read_password()
    if password is None:
        return 1

    email = args.email.strip().lower()
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO admin_users (email, password_hash, name, role)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (email) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    name = EXCLUDED.name,
                    is_active = TRUE
            RETURNING id, (created_at = now()) AS just_created
            """,
            email,
            hash_password(password),
            args.name,
            args.role,
        )
    finally:
        await conn.close()

    verb = "created" if row["just_created"] else "updated (password reset, re-activated)"
    print(f"Admin {verb}: {email} (id={row['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
