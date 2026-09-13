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
import urllib.parse

import asyncpg
from dotenv import load_dotenv

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SQL_FILE = BACKEND / "scripts" / "grant_app_role.sql"

APPEND_ONLY = ("audit_events", "governance_request_events")


def _dsn() -> tuple[str, str, str]:
    """(dsn, host, dbname). The DSN is handed to asyncpg verbatim, deliberately.

    IT USED TO BE PICKED APART WITH A REGEX and passed as discrete user/password/host
    arguments, which breaks on any password that is percent-encoded in the URL — and it
    has to be percent-encoded, because a Postgres password containing `@`, `#` or `$`
    cannot appear literally in a DSN. asyncpg decodes a DSN it is given; it does not
    decode a `password=` keyword argument. So a password written `p%24ss` in the DSN
    reached the server with the escape still in it, as the literal characters, and
    authentication failed — reported as a pg_hba/encryption error that points nowhere
    near the real cause.

    Parsing here is only for the log line.
    """
    load_dotenv(BACKEND / ".env")
    raw = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING")
    if not raw:
        sys.exit("POSTGRES_MIGRATIONS_CONN_STRING is not set in backend/.env")
    dsn = raw.replace("+asyncpg", "").replace("+psycopg", "")
    parsed = urllib.parse.urlparse(dsn)
    return dsn, parsed.hostname or "?", (parsed.path or "/?").lstrip("/")


async def main() -> None:
    dsn, host, dbname = _dsn()
    print(f"applying grants to {dbname} on {host}")

    statements = [
        s.strip()
        for s in SQL_FILE.read_text(encoding="utf-8").split(";")
        if s.strip() and not all(ln.strip().startswith("--") for ln in s.strip().splitlines())
    ]

    # Azure Postgres Flexible Server refuses plaintext; a local server usually offers no
    # TLS at all. Try encrypted first and fall back ONLY when the server turns out not to
    # support TLS — never on an auth failure, which an unconditional retry would hide
    # behind a misleading "no encryption" message from the second attempt.
    try:
        conn = await asyncpg.connect(dsn, ssl="require", timeout=30)
    except (asyncpg.exceptions.InvalidAuthorizationSpecificationError, OSError) as exc:
        if "does not support SSL" not in str(exc) and "server does not support" not in str(exc):
            raise
        conn = await asyncpg.connect(dsn, timeout=30)
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
