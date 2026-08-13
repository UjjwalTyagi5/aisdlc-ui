# CI gate: grep -r "MemorySaver" agentic_app/agents_orchestrator/ must return zero results
# after this module is the sole checkpointer factory.

# PostgresSaver uses psycopg3 (POSTGRES_SYNC_CONN_STRING: postgresql://...)
# ORM engine uses asyncpg (POSTGRES_CONN_STRING: postgresql+asyncpg://...)
# These are SEPARATE variables serving SEPARATE drivers. Do not conflate them.
"""Central checkpointer factory for all LangGraph agent graphs.

Usage:
    from config.checkpoint import build_checkpointer
    _checkpointer = build_checkpointer("requirements")
    app = workflow.compile(checkpointer=_checkpointer)

Thread ID format {agent_type}:{session_id} (e.g. requirements:sess_abc123) is set by
calling code when invoking the compiled graph — NOT in this module.

In enterprise mode (AGENT_RUNTIME_MODE=enterprise) the function raises RuntimeError if
PostgresSaver setup fails — no silent MemorySaver fallback.
In local mode (default) it falls back to MemorySaver with a warning when
POSTGRES_SYNC_CONN_STRING is not set or connection fails.
"""
import logging
from typing import Dict

from langgraph.checkpoint.memory import MemorySaver

from config.env import AGENT_RUNTIME_MODE, AZURE_KEY_VAULT_URL, KV_SECRET_POSTGRES_SYNC_CONN
from config.env import POSTGRES_SYNC_CONN_STRING as _POSTGRES_SYNC_CONN_STRING_ENV

logger = logging.getLogger(__name__)


def _resolve_sync_conn_str() -> str:
    """LangGraph checkpointer DSN — Key-Vault-first (secret name KV_SECRET_POSTGRES_SYNC_CONN
    from config.env; defaults to sdlc-{env}-postgres-sync-conn-string, overridable via .env),
    env fallback. Resolved once at import (the checkpointer uses psycopg/sync)."""
    if AZURE_KEY_VAULT_URL:
        from shared.keyvault import load_secret_sync

        val = load_secret_sync(KV_SECRET_POSTGRES_SYNC_CONN)
        if val:
            logger.info("Checkpointer DSN loaded from Key Vault")
            return val
    return _POSTGRES_SYNC_CONN_STRING_ENV


POSTGRES_SYNC_CONN_STRING = _resolve_sync_conn_str()

# Registry: agent_name -> "postgres" | "memory"
_registry: Dict[str, str] = {}


# Shared async pool for the enterprise durable checkpointer (opened at startup via
# aopen_checkpointer). None in local mode — MemorySaver needs no pool.
_async_pool = None


def _build_async_pool():
    from psycopg_pool import AsyncConnectionPool  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    return AsyncConnectionPool(
        conninfo=POSTGRES_SYNC_CONN_STRING,
        max_size=20,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )


def build_checkpointer(agent_name: str):
    """Return a ready checkpointer for the (async) agent graphs.

    The agent graphs are invoked via astream/ainvoke, so the checkpointer MUST
    implement the async API. The SYNC PostgresSaver raises NotImplementedError on
    aget_tuple — it can never back these graphs (that was the cause of the chat
    "streaming error"). So:
      - local  → MemorySaver (async-safe). Execution state is in-process, but the
        human-facing transcript (ConversationSession/Message) is durable in Postgres
        regardless, so chat history survives restarts. psycopg-async durable
        checkpointing is also impractical on Windows (ProactorEventLoop is required
        for the dev agent's subprocesses but is incompatible with psycopg async).
      - enterprise → AsyncPostgresSaver (durable, async; Linux/SelectorEventLoop),
        backed by a pool opened once at startup via aopen_checkpointer().
    """
    if AGENT_RUNTIME_MODE != "enterprise":
        _registry[agent_name] = "memory"
        logger.info("[%s] checkpointer: MemorySaver (local)", agent_name)
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore  # noqa: PLC0415

        global _async_pool
        if _async_pool is None:
            _async_pool = _build_async_pool()
        cp = AsyncPostgresSaver(_async_pool)
        _registry[agent_name] = "postgres-async"
        logger.info("[%s] checkpointer: AsyncPostgresSaver", agent_name)
        return cp
    except Exception as exc:
        raise RuntimeError(
            f"[{agent_name}] AsyncPostgresSaver required in enterprise mode but setup failed: {exc}"
        ) from exc


async def aopen_checkpointer() -> None:
    """Open the async checkpointer pool + ensure tables. Call once at process startup
    (FastAPI lifespan + worker main). No-op in local mode (no pool)."""
    if _async_pool is None:
        return
    await _async_pool.open()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: PLC0415

        await AsyncPostgresSaver(_async_pool).setup()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Checkpointer setup skipped: %s", exc)
    else:
        logger.info("AsyncPostgresSaver pool opened + schema ready")


async def aclose_checkpointer() -> None:
    """Close the async checkpointer pool at process shutdown. No-op in local mode."""
    if _async_pool is None:
        return
    try:
        await _async_pool.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Checkpointer pool close failed: %s", exc)


def get_checkpointer_status() -> Dict[str, str]:
    """Return a snapshot of which checkpointer type each agent is using."""
    return dict(_registry)
