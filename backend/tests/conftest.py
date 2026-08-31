"""Root-level test fixtures for agentic_app tests.

Provides:
  mint_token       — callable fixture that mints a real signed HS256 JWT with a
                     controllable permissions array.  Used by negative-authz tests
                     so they exercise the 403 branch rather than the 401 branch that
                     a literal invalid-token string would trigger (Open Question 1
                     resolution, milestone-7.2 RESEARCH).
  mint_rs256_token — callable fixture that generates an RSA keypair, signs an RS256
                     JWT emitting the PRODUCTION nested ``https://sdlc/tenant``
                     claim (session.ts:30-32), and returns the token + public key
                     material for JWKS mocking.  Wave-0 OIDC test scaffold
                     (milestone-7.3 plan-01).
"""
from __future__ import annotations

import os

import pytest

from config.env import JWT_SECRET_KEY


# ─────────────────────────────────────────────────────────────────────────────
# Throwaway-tenant cleanup
# ─────────────────────────────────────────────────────────────────────────────

def _migrations_dsn() -> str | None:
    """The superuser/BYPASSRLS DSN, in asyncpg form. None when unset."""
    dsn = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING", "")
    if not dsn:
        return None
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


async def _org_ids(conn) -> set[str]:
    return {str(r["id"]) for r in await conn.fetch("SELECT id FROM organizations")}


async def _purge_tenants(conn, org_ids: set[str]) -> None:
    """Delete these organizations and everything belonging to them.

    Whole tenants only, so the result stays referentially consistent — nothing
    outside the set points into it. `session_replication_role = replica` skips FK
    trigger ordering for the transaction, which is what lets the deletes run in
    catalogue order instead of requiring a hand-maintained dependency graph. It also
    means a table added later is cleaned up without editing this helper.

    Requires a BYPASSRLS role: the app role is NOBYPASSRLS and these tables are
    FORCE RLS, so rows would be invisible to the DELETE without the tenant GUC set
    per table.
    """
    if not org_ids:
        return
    ids = list(org_ids)
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = 'replica'")
        tables = await conn.fetch(
            """
            SELECT c.table_name
              FROM information_schema.columns c
              JOIN information_schema.tables t
                ON t.table_name = c.table_name AND t.table_schema = c.table_schema
             WHERE c.table_schema = 'public'
               AND c.column_name = 'tenant_id'
               AND t.table_type = 'BASE TABLE'
            """
        )
        for row in tables:
            await conn.execute(
                f'DELETE FROM "{row["table_name"]}" WHERE tenant_id = ANY($1::uuid[])', ids
            )
        await conn.execute(
            "DELETE FROM workspaces WHERE organization_id = ANY($1::uuid[])", ids
        )
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", ids)


@pytest.fixture(autouse=True)
def _restore_ws_context_vars():
    """Put config.ws_helper's ContextVars back after every test.

    A SAFETY NET, not the fix. The real fix is for a test that sets one to reset it —
    `set_session_id` returns a reset token precisely so it can — and
    tests/development/test_workspace_binding.py now does.

    But eight test modules set these, `set_session_id(x)` reads like a plain setter,
    and the token it hands back is easy to drop. When one leaks, the failure surfaces
    somewhere else entirely: three tests in tests/pipeline/test_pipeline_session.py
    opened with `assert get_session_id() is None`, passed on their own, and failed only
    in a full run — pointing at the pipeline bridge, which was innocent.

    Cheap enough to be unconditional: six ContextVar reads before and six writes after,
    per test, and only when a value actually changed.
    """
    from config import ws_helper

    names = ("SESSION_ID", "USER_ID", "PROVIDER_KIND", "TENANT_ID", "PROJECT_ID", "RUN_ID")
    before = {n: getattr(ws_helper, n).get() for n in names}
    try:
        yield
    finally:
        for n in names:
            var = getattr(ws_helper, n)
            if var.get() != before[n]:
                var.set(before[n])


@pytest.fixture(scope="session")
async def _cleanup_conn():
    """ONE asyncpg connection, reused by every purge_created_orgs teardown.

    WHY THIS EXISTS. purge_created_orgs used to open a fresh connection to snapshot
    the org ids and a second one to purge — two connects per test, across 570 tests in
    114 modules, so 1,140 connections per full run. Against this project's local
    Postgres `asyncpg.connect` + close measures ~132 ms, where a local connect is
    normally 5-15 ms; the rest is Windows loopback plus scram-sha-256.

    MEASURED SAVING: 113.7 ms per test, from an A/B over 7 modules / 96 tests, two
    runs each arm, nothing else touching the database — 102.85s before, 91.93s after.
    That is ~65s off a ~21 minute suite, about 5%.

    Worth stating because the estimate that motivated this was ~2.7 minutes, built by
    assuming the whole ~290 ms fixture floor would vanish. It did not: inside a real
    run some of the connect cost overlaps with work the test is doing anyway, so a
    tight-loop microbenchmark overstated it by roughly 2.5x. The suite is DB-bound
    (~56.6k transactions through one backend at ~49% CPU) and connection setup was
    never the largest part of that — moving it further needs fewer queries or
    pytest-xdist with per-worker databases, not more connection tuning.

    Session-scoped is safe here because pytest.ini sets
    `asyncio_default_fixture_loop_scope = session`: every fixture body — including the
    function-scoped one below — runs on the session event loop, so the connection is
    always awaited from the loop that created it. That is the same constraint
    shared/db.py documents for NullPool and config/connectors/http_client.py for its
    per-loop client cache; get it wrong and asyncpg raises from a foreign loop.

    Yields a callable rather than the connection itself so a teardown that finds the
    socket dead — a test that restarted the server, or an idle timeout — reconnects
    instead of failing the run. Cleanup must never be the thing that breaks a suite.
    """
    dsn = _migrations_dsn()
    if dsn is None:
        yield None
        return

    import asyncpg

    holder: dict = {"conn": None}

    async def _conn():
        c = holder["conn"]
        if c is None or c.is_closed():
            c = holder["conn"] = await asyncpg.connect(dsn)
        return c

    try:
        yield _conn
    finally:
        c = holder["conn"]
        if c is not None and not c.is_closed():
            await c.close()


@pytest.fixture
async def purge_created_orgs(_cleanup_conn):
    """Remove any organization a test creates, however it creates it.

    Snapshot-and-diff rather than a registry: these suites call
    `provision_organization` inline and would each need editing to register an id,
    and a registration call is exactly the line someone forgets in the next test.
    Anything that appeared while the test ran is cleaned up.

    Applied per module with:

        pytestmark = pytest.mark.usefixtures("purge_created_orgs")

    Skips silently without POSTGRES_MIGRATIONS_CONN_STRING — the suites that use it
    already skip without a database, and cleanup must never be the thing that fails.
    """
    if _cleanup_conn is None:
        yield
        return

    conn = await _cleanup_conn()
    before = await _org_ids(conn)

    yield

    conn = await _cleanup_conn()  # reconnects if the socket died during the test
    created = await _org_ids(conn) - before
    if not created:
        # Nothing to undo. The orphan-users DELETE below used to run unconditionally,
        # costing ~23 ms of every teardown to delete nothing: an orphan is a users row
        # whose organization was purged, so one cannot appear unless this test created
        # an organization in the first place.
        return

    await _purge_tenants(conn, created)
    # `grant_role` upserts a users row with a NULL email, so a throwaway tenant
    # leaves users behind pointing at an organization that no longer exists.
    # They are invisible to the org diff above and accumulate silently.
    await conn.execute(
        """
        DELETE FROM users u
         WHERE u.tenant_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM organizations o WHERE o.id = u.tenant_id)
        """
    )


@pytest.fixture
def mint_token():
    """Return a callable that produces a real signed HS256 JWT.

    Why a real token: a literal string like 'wrong-role-developer-token' is not
    a valid JWT — the middleware would 401, not 403.  Negative-authz tests need
    a decodable token that simply lacks the required permission (T-7.2-04).

    Usage:
        token = mint_token(user_id="u1", tenant_id="tid-abc", permissions=["run:create"])
        headers = {"Authorization": f"Bearer {token}"}
    """
    from datetime import datetime, timedelta

    import jwt as pyjwt

    def _mint(
        user_id: str = "test-user-001",
        tenant_id: str = "00000000-0000-0000-0000-000000000001",
        permissions: list[str] | None = None,
    ) -> str:
        payload = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "permissions": permissions if permissions is not None else [],
            "exp": datetime.utcnow() + timedelta(minutes=60),
        }
        return pyjwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")

    return _mint


@pytest.fixture
def mint_rs256_token():
    """Return a callable that produces a real RS256-signed JWT with the PRODUCTION nested claim.

    Why nested claim: the Auth0 Action injects ``https://sdlc/tenant = {id, name}``
    (confirmed apps/web/lib/auth/session.ts:30-32).  There is NO flat top-level
    ``tenant_id`` in the real Auth0 token — the extract_tenant_id adapter (providers.py)
    is the only extraction path (T-7.3-02 seam).

    Returns a callable with signature:
        mint(
            user_id: str = "test-user-001",
            tenant_id: str = "00000000-0000-0000-0000-000000000001",
            tenant_name: str = "Acme",
            include_tenant_claim: bool = True,
        ) -> tuple[str, public_key_object, str]

    Return shape: (token: str, public_key, tenant_id: str)
      - token       — 3-segment RS256 JWT string
      - public_key  — cryptography RSAPublicKey object; use as mock_signing_key.key in tests
      - tenant_id   — the UUID string injected into the nested claim (for assertion convenience)

    The same RSA keypair is generated once per test invocation (function scope).
    Reset ``_jwks_client`` global between RS256 cases to prevent cross-test leakage
    (Pitfall 5): ``patch.object(jwt_mod, "_jwks_client", None)``.

    Usage:
        token, public_key, tenant_id = mint_rs256_token()
        mock_key = MagicMock(); mock_key.key = public_key
        with patch.object(jwt_mod, "_jwks_client", mock_jwks):
            ...
    """
    from datetime import datetime, timedelta

    import jwt as pyjwt
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate a fresh RSA keypair for this test invocation
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()

    def _mint(
        user_id: str = "test-user-001",
        tenant_id: str = "00000000-0000-0000-0000-000000000001",
        tenant_name: str = "Acme",
        include_tenant_claim: bool = True,
    ):
        """Mint an RS256 JWT.

        When include_tenant_claim=False the nested https://sdlc/tenant key is
        omitted entirely — used to exercise the missing-claim → None path
        (test_missing_tenant_id_401 in plan-02).
        """
        now = datetime.utcnow()
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=60),
            "aud": "test-audience",
            "iss": "https://test.auth0.com/",
        }
        if include_tenant_claim:
            # PRODUCTION Auth0 Action nested claim shape (session.ts:30-32).
            # Do NOT emit a flat tenant_id — the adapter is the only extraction path.
            payload["https://sdlc/tenant"] = {"id": tenant_id, "name": tenant_name}

        token = pyjwt.encode(payload, private_key, algorithm="RS256")
        return token, public_key, tenant_id

    return _mint
