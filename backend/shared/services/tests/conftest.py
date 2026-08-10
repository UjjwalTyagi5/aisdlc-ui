"""Test configuration for shared.services tests.

Sets asyncio loop scope to 'session' so all tests share a single event loop.
Required because shared.db uses a module-level SQLAlchemy async engine — per-test
event loops leave pool connections referencing a closed loop on subsequent tests.
"""
import pytest_asyncio
from sqlalchemy import delete

from shared.db import get_db_session_superuser
from shared.models.orm import AgentSession, OrchestratorState

# All session_ids and chat_session_ids used by test_agent_session_store.py.
_AGENT_SESSION_IDS = [
    "test-sess-design-1",
    "test-sess-partial-1",
    "test-sess-coerce-1",
    "test-sess-allowlist-1",
]
_CHAT_SESSION_IDS = [
    "test-chat-1",
]


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_test_rows():
    """Delete all rows created by the store tests before each test runs.

    autouse=True means every test in this package gets a clean slate without
    having to request the fixture explicitly.  We run the DELETE *before* the
    test body (yield boundary) so a crashed previous run cannot leave residue
    that causes a false-pass on the next run.

    loop_scope="session" is required: it must match the pytestmark on the test
    module so the fixture runs inside the same shared event loop as the tests
    (asyncpg connections are bound to a specific event loop).
    """
    async with get_db_session_superuser() as session:
        await session.execute(
            delete(AgentSession).where(
                AgentSession.session_id.in_(_AGENT_SESSION_IDS)
            )
        )
        await session.execute(
            delete(OrchestratorState).where(
                OrchestratorState.chat_session_id.in_(_CHAT_SESSION_IDS)
            )
        )
    yield
