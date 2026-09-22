"""Make the TEST database's app connection a restricted role, so RLS is real there.

    cd backend
    uv run python -m scripts.setup_test_app_role

WHAT IT FIXES. Roughly a third of the backend suite asserts tenant isolation, and it
does so the way the application experiences it: `_events(org_id)` opens a session for
one tenant and selects from `audit_events` with NO tenant filter, because the row-level
policy is supposed to be the filter. Against a SUPERUSER that policy is not applied at
all — `postgres` is BYPASSRLS — so those tests read every tenant's rows and fail with
counts like `176 == 1`, plus `test_app_role_is_not_bypassrls`,
`test_migrations_dsn_is_not_app_dsn` and the append-only audit tests, which say outright
what is wrong. Nothing is broken in the application; the test connection is simply not
the kind of connection the application uses.

So: give `sdlc_product_test` the same two-role shape production has — `postgres` for
migrations (it must create the policies), `sdlc_app` for the app (NOSUPERUSER,
NOBYPASSRLS, so the policies bind) — and point `backend/.env.test` at it.

Idempotent. Re-run after any `alembic upgrade` that creates tables in the test database,
for the same reason `scripts/grant_app_role` has to be re-run against the real one.

REFUSES A DATABASE THAT IS NOT A TEST DATABASE. It rewrites .env.test and changes a role
password; pointing it at `sdlc_product` would repoint the development app at a role
whose password only this script knows.
"""
from __future__ import annotations

import pathlib
import re
import secrets
import sys
import urllib.parse

import asyncio
import asyncpg

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ENV_TEST = BACKEND / ".env.test"
GRANT_SQL = BACKEND / "scripts" / "grant_app_role.sql"
APP_ROLE = "sdlc_app"
APPEND_ONLY = ("audit_events", "governance_request_events")

#: How asyncpg reports a server that offers no TLS — the only case where retrying in
#: plaintext is right. Anything else (a bad password above all) must surface as itself.
_NO_TLS_SIGNS = ("does not support SSL", "server does not support", "rejected SSL upgrade")


def _read_env(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _plain(dsn: str) -> str:
    """A SQLAlchemy URL as libpq wants it — asyncpg does not know the +driver form."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)


def _swap_credentials(dsn: str, user: str, password: str) -> str:
    """The same DSN as another role. The password is percent-encoded: a Postgres
    password containing @ or / splits a URL at the wrong character otherwise."""
    return re.sub(
        r"^(postgresql(?:\+\w+)?://)[^@]*@",
        lambda m: f"{m.group(1)}{user}:{urllib.parse.quote(password, safe='')}@",
        dsn,
    )


def _database_name(dsn: str) -> str:
    m = re.search(r"/([^/?]+)(?:\?|$)", dsn or "")
    return m.group(1) if m else ""


async def main() -> None:
    if not ENV_TEST.exists():
        sys.exit(f"{ENV_TEST} does not exist — see docs/local-setup.md, 'Test database'.")

    env = _read_env(ENV_TEST)
    migrations_dsn = env.get("POSTGRES_MIGRATIONS_CONN_STRING", "")
    if not migrations_dsn:
        sys.exit("POSTGRES_MIGRATIONS_CONN_STRING is not set in backend/.env.test")

    dbname = _database_name(migrations_dsn)
    if "test" not in dbname.lower():
        sys.exit(
            f"refusing to run against {dbname!r}: this script rewrites .env.test and sets "
            f"{APP_ROLE}'s password, which is only ever right for a test database."
        )

    password = secrets.token_urlsafe(24)
    print(f"setting up {APP_ROLE} on {dbname}")

    # Encrypted first, plaintext only when the server says it has no TLS at all — never
    # on an auth failure, which an unconditional retry would hide behind a misleading
    # "no encryption" message. Same ladder as scripts/grant_app_role.py, for the same
    # reason: Azure Flexible Server refuses plaintext, a local server usually offers no
    # TLS. asyncpg words that refusal more than one way; a native Windows Postgres says
    # "rejected SSL upgrade", which the first version of this ladder did not recognise.
    try:
        conn = await asyncpg.connect(_plain(migrations_dsn), ssl="require", timeout=30)
    except (asyncpg.exceptions.InvalidAuthorizationSpecificationError, OSError) as exc:
        if not any(sign in str(exc) for sign in _NO_TLS_SIGNS):
            raise
        conn = await asyncpg.connect(_plain(migrations_dsn), timeout=30)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE)
        if exists:
            # NOSUPERUSER / NOBYPASSRLS are re-asserted, not assumed: a role that picked
            # up either one somewhere else would silently disable every policy below.
            await conn.execute(
                f'ALTER ROLE "{APP_ROLE}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB '
                f"PASSWORD '{password}'"
            )
            print(f"  role {APP_ROLE} updated (login, nosuperuser, nobypassrls)")
        else:
            await conn.execute(
                f'CREATE ROLE "{APP_ROLE}" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB '
                f"PASSWORD '{password}'"
            )
            print(f"  role {APP_ROLE} created")

        await conn.execute(f'GRANT CONNECT ON DATABASE "{dbname}" TO "{APP_ROLE}"')

        # A chunk is dropped only when EVERY line in it is a comment. Testing
        # `startswith("--")` on the chunk instead would discard each statement that has
        # an explanatory comment above it — which, in that file, is all of them.
        statements = [
            s.strip() for s in GRANT_SQL.read_text(encoding="utf-8").split(";")
            if s.strip() and not all(ln.strip().startswith("--") for ln in s.strip().splitlines())
        ]
        for statement in statements:
            await conn.execute(statement)
        print(f"  {len(statements)} grant statement(s) applied")

        # VERIFY, because every failure here is silent. A superuser app role reads every
        # tenant's rows and nothing errors; an audit table the app can UPDATE stops being
        # evidence and nothing errors either.
        row = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", APP_ROLE
        )
        if row["rolsuper"] or row["rolbypassrls"]:
            sys.exit(f"{APP_ROLE} is superuser or BYPASSRLS — tenant policies would not apply")
        print(f"  {APP_ROLE} is not superuser and not BYPASSRLS")

        unreadable = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND NOT has_table_privilege($1, schemaname||'.'||tablename, 'SELECT')
            ORDER BY tablename
            """,
            APP_ROLE,
        )
        if unreadable:
            sys.exit(
                f"{APP_ROLE} cannot SELECT: " + ", ".join(r["tablename"] for r in unreadable)
            )
        print(f"  every table is readable by {APP_ROLE}")

        for table in APPEND_ONLY:
            for privilege in ("UPDATE", "DELETE"):
                if await conn.fetchval(
                    "SELECT has_table_privilege($1, $2, $3)", APP_ROLE, table, privilege
                ):
                    sys.exit(f"{APP_ROLE} can {privilege} {table} — it must be append-only")
            print(f"  {table} is append-only (no UPDATE, no DELETE)")
    finally:
        await conn.close()

    app_dsn = _swap_credentials(env["POSTGRES_CONN_STRING"], APP_ROLE, password)
    sync_dsn = _swap_credentials(env["POSTGRES_SYNC_CONN_STRING"], APP_ROLE, password)

    backup = ENV_TEST.with_suffix(".test.bak")
    backup.write_text(ENV_TEST.read_text(encoding="utf-8"), encoding="utf-8")

    text = ENV_TEST.read_text(encoding="utf-8")
    text = re.sub(r"^POSTGRES_CONN_STRING=.*$", f"POSTGRES_CONN_STRING={app_dsn}",
                  text, flags=re.M)
    text = re.sub(r"^POSTGRES_SYNC_CONN_STRING=.*$", f"POSTGRES_SYNC_CONN_STRING={sync_dsn}",
                  text, flags=re.M)
    ENV_TEST.write_text(text, encoding="utf-8")

    print(f"\n.env.test now connects as {APP_ROLE} (migrations still run as the admin role)")
    print(f"previous file saved as {backup.name}")
    print("run the suite again: the tenant-isolation tests now exercise a real boundary")


if __name__ == "__main__":
    asyncio.run(main())
