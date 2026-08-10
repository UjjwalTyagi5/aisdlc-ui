"""SQLAlchemy async engine and session factory for agentic_app.

Import POSTGRES_CONN_STRING from config.env only.
POSTGRES_CONN_STRING must use the postgresql+asyncpg:// prefix — not postgresql:// or postgresql+psycopg://.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config.env import AZURE_KEY_VAULT_URL, KV_SECRET_POSTGRES_CONN, POSTGRES_CONN_STRING
import shared.azure_credential  # noqa: F401 — import quiets Azure SDK logging before the KV call

logger = logging.getLogger(__name__)

# Placeholder used only when neither Key Vault nor env supplies a URL. The engine defers
# all TCP connections to the first query, so a placeholder is safe at import time.
_PLACEHOLDER = "postgresql+asyncpg://placeholder:placeholder@localhost:5432/sdlc_agentic"


def _resolve_conn_str() -> str:
    """Resolve the app DB connection string KEY-VAULT-FIRST, env as fallback.

    The DB password lives in Key Vault under the secret name KV_SECRET_POSTGRES_CONN
    (config.env; defaults to 'sdlc-{env}-postgres-conn-string', overridable via .env) — not
    in .env itself. This reads it synchronously at import via DefaultAzureCredential (az login
    locally / Managed Identity in prod). Falls back to the POSTGRES_CONN_STRING env var only
    if Key Vault is unset or unreachable (e.g. az login expired) — a deliberate resilience
    path, not the primary source.
    """
    if AZURE_KEY_VAULT_URL:
        from shared.keyvault import load_secret_sync

        value = load_secret_sync(KV_SECRET_POSTGRES_CONN)
        if value:
            logger.info("DB connection string loaded from Key Vault (%s)", KV_SECRET_POSTGRES_CONN)
            return value
        logger.warning(
            "Key Vault DB conn string unavailable — falling back to POSTGRES_CONN_STRING env"
        )
    return POSTGRES_CONN_STRING or _PLACEHOLDER


_conn_str = _resolve_conn_str()

# Public alias so other modules (e.g. the /health probe, the enterprise BYPASSRLS guard)
# use the SAME resolved string instead of re-reading POSTGRES_CONN_STRING from env —
# which is intentionally KV-sourced now and absent from .env.
RESOLVED_POSTGRES_CONN_STRING = _conn_str

# NullPool (no cross-checkout connection reuse). The LangGraph agents execute their
# tools inside a fresh asyncio.run() loop (see the requirements agent's `action` node):
# an asyncpg connection is bound to the loop that created it, so a connection opened in
# that transient sub-loop (e.g. a tool resolving a secret/PAT) gets orphaned when the
# loop closes and then crashes the next main-loop checkout with "Event loop is closed" /
# "got Future attached to a different loop". NullPool opens a fresh connection per session
# on the CURRENT loop and disposes it on release, making DB access loop-safe across the
# uvicorn main loop and the agents' sub-loops. (Front a pgbouncer in prod if pooling is
# needed — the app-level pool cannot be shared across these loops.)
engine = create_async_engine(
    _conn_str,
    poolclass=NullPool,
    echo=False,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields one AsyncSession per request with commit/rollback lifecycle.

    Reads tenant_id from request.state (set by auth middleware) and issues
    SET LOCAL app.current_tenant_id so RLS policies filter to the authenticated tenant.
    The GUC is reset to '' in finally as defense against pooled-connection bleed (Pitfall 1).
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    async with AsyncSessionFactory() as session:
        try:
            if tenant_id:
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # Failsafe: reset so a pooled connection is RLS-blocked if reused without context.
            await session.execute(text("SET LOCAL app.current_tenant_id = ''"))


@asynccontextmanager
async def get_db_session_for_tenant(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager — yields a session scoped to the given tenant.

    For background tasks with no request object. Always sets SET LOCAL
    app.current_tenant_id from the explicit tenant_id argument.
    The GUC is reset to '' in finally as defense against pooled-connection bleed (Pitfall 1).
    Use when deriving tenant from the entity being processed (D-04).

    Usage:
        async with get_db_session_for_tenant(tenant_id) as session:
            ...
    """
    async with AsyncSessionFactory() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # Failsafe: reset so a pooled connection is RLS-blocked if reused without context.
            await session.execute(text("SET LOCAL app.current_tenant_id = ''"))


@asynccontextmanager
async def get_db_session_superuser() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager — yields a session without tenant GUC.

    For system-only cross-tenant operations. The caller's DB role must be
    BYPASSRLS or superuser for this session to access rows across all tenants.

    NEVER call from agent tool code or connector paths. Reserved ONLY for:
      - Health checks
      - Platform metrics
      - Migration-adjacent startup checks
    See D-05 in CONTEXT.md. Any other use is a security violation.

    Usage:
        async with get_db_session_superuser() as session:
            ...
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
