"""Apply scripts/grant_app_role.sql, then prove it took. No psql required.

    uv run python -m scripts.grant_app_role

WHY THIS EXISTS RATHER THAN "just run psql". `psql` ships with the PostgreSQL installer
but is not added to PATH on Windows, so the first thing a new machine does with a psql
command is fail at `The term 'psql' is not recognized`. Everyone setting this up already
has the backend's virtualenv, because they need it for alembic — so use that.

Connects with POSTGRES_MIGRATIONS_CONN_STRING (the `postgres` superuser), because
granting requires the role that owns the tables.

IT VERIFIES AFTERWARDS, and that is half the point. The failure this guards against is
silent: if the two REVOKEs at the end of the SQL are lost, nothing errors and the audit
trail merely stops being append-only. A grant script that does not check its own work
would let that through.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys

import asyncpg
from dotenv import load_dotenv

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SQL_FILE = BACKEND / "scripts" / "grant_app_role.sql"

APPEND_ONLY = ("audit_events", "governance_request_events")


def _dsn() -> tuple[str, str, str, int, str]:
    load_dotenv(BACKEND / ".env")
    raw = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING")
    if not raw:
        sys.exit("POSTGRES_MIGRATIONS_CONN_STRING is not set in backend/.env")
    url = raw.replace("+asyncpg", "")
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:/]+):(\d+)/(\w+)", url)
    if not m:
        sys.exit(f"could not parse POSTGRES_MIGRATIONS_CONN_STRING: {url!r}")
    return m.group(1), m.group(2), m.group(3), int(m.group(4)), m.group(5)


async def main() -> None:
    user, password, host, port, dbname = _dsn()
    print(f"applying grants to {dbname} on {host}:{port} as {user}")

    statements = [
        s.strip()
        for s in SQL_FILE.read_text(encoding="utf-8").split(";")
        if s.strip() and not all(ln.strip().startswith("--") for ln in s.strip().splitlines())
    ]

    conn = await asyncpg.connect(
        user=user, password=password, host=host, port=port, database=dbname
    )
    try:
        for stmt in statements:
            await conn.execute(stmt)
        print(f"  {len(statements)} statement(s) applied")

        unreadable = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND NOT has_table_privilege('sdlc_app', schemaname||'.'||tablename, 'SELECT')
            ORDER BY tablename
            """
        )
        writable = await conn.fetch(
            """
            SELECT tablename,
                   has_table_privilege('sdlc_app', schemaname||'.'||tablename, 'UPDATE') AS upd,
                   has_table_privilege('sdlc_app', schemaname||'.'||tablename, 'DELETE') AS del
            FROM pg_tables
            WHERE schemaname = 'public' AND tablename = ANY($1::text[])
            """,
            list(APPEND_ONLY),
        )
    finally:
        await conn.close()

    problems: list[str] = []

    if unreadable:
        problems.append(
            "sdlc_app cannot SELECT: " + ", ".join(r["tablename"] for r in unreadable)
        )
    else:
        print("  every table is readable by sdlc_app")

    seen = {r["tablename"] for r in writable}
    for missing in set(APPEND_ONLY) - seen:
        # Not fatal: a database migrated before that table existed simply has nothing to
        # revoke yet. Worth saying out loud rather than silently passing.
        print(f"  note: {missing} does not exist yet — nothing to revoke")
    for row in writable:
        if row["upd"] or row["del"]:
            problems.append(
                f"{row['tablename']} is NOT append-only "
                f"(update={row['upd']} delete={row['del']})"
            )
        else:
            print(f"  {row['tablename']} is append-only (no UPDATE, no DELETE)")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\ngrants applied and verified")


if __name__ == "__main__":
    asyncio.run(main())
