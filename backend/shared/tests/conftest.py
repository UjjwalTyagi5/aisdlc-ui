import os
import re

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from config.env import (
    AZURE_BLOB_ACCOUNT_URL,
    POSTGRES_CONN_STRING,
    POSTGRES_MIGRATIONS_CONN_STRING,
    REDIS_URL,
)

# Names that mark a database as safe to DESTROY. Anything else is presumed to be
# somebody's real data.
_DISPOSABLE_HINTS = ("test", "tmp", "temp", "scratch", "ci", "throwaway")
_OPT_IN = "ALLOW_DESTRUCTIVE_DB_TESTS"


def _database_name(dsn: str) -> str:
    """The database name out of a SQLAlchemy/libpq URL, minus any query string."""
    m = re.search(r"/([^/?]+)(?:\?|$)", dsn or "")
    return m.group(1) if m else ""


@pytest.fixture
def disposable_migrations_dsn():
    """A migrations DSN this test is allowed to DROP EVERY TABLE in.

    WHY THIS EXISTS. `test_alembic_migration_cycle` runs `alembic downgrade base`,
    which drops the entire schema. It took the DSN straight from the environment and
    the only guard was "is it set", so pointing it at a development database -- which
    is the normal thing for POSTGRES_MIGRATIONS_CONN_STRING to hold -- silently
    destroyed it. That happened on 2026-09-13: one organization, one business unit,
    one project, the Langfuse bindings and 125 audit events, gone in a run that
    reported itself as a single ordinary test failure.

    A test that can do that must name the database it is willing to ruin. The DSN is
    accepted only when the database name looks disposable, or when the caller opts in
    explicitly with ALLOW_DESTRUCTIVE_DB_TESTS=1 -- which is deliberately awkward to
    type by accident and greppable when someone wonders how it got set.
    """
    dsn = POSTGRES_MIGRATIONS_CONN_STRING
    if not dsn:
        pytest.skip("POSTGRES_MIGRATIONS_CONN_STRING not set")

    name = _database_name(dsn)
    if os.environ.get(_OPT_IN) == "1":
        return dsn
    if not any(hint in name.lower() for hint in _DISPOSABLE_HINTS):
        pytest.skip(
            f"refusing to run a schema-destroying test against database {name!r}: "
            f"the name does not look disposable. This test runs `alembic downgrade "
            f"base`, which DROPS EVERY TABLE. Point "
            f"POSTGRES_MIGRATIONS_CONN_STRING at a throwaway database, or set "
            f"{_OPT_IN}=1 if you genuinely mean to destroy {name!r}."
        )
    return dsn


@pytest.fixture
def pg_conn_string():
    if not POSTGRES_CONN_STRING:
        pytest.skip("POSTGRES_CONN_STRING not set")
    return POSTGRES_CONN_STRING


@pytest.fixture
def redis_url_str():
    if not REDIS_URL:
        pytest.skip("REDIS_URL not set")
    return REDIS_URL


def _is_loop_teardown_noise(exc: BaseException) -> bool:
    """Is this the known Windows event-loop-vs-asyncpg teardown race, and only that?

    Matched on the message so that a real RuntimeError or AttributeError raised while
    closing a session still fails the test. The two shapes the race takes:

        RuntimeError:   Event loop is closed
        AttributeError: 'NoneType' object has no attribute 'send'   (transport gone)
    """
    msg = str(exc)
    return "Event loop is closed" in msg or "'NoneType' object has no attribute 'send'" in msg


@pytest_asyncio.fixture
async def db_session(pg_conn_string):
    # NullPool prevents connection pooling between tests — each test gets a fresh connection
    engine = create_async_engine(pg_conn_string, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        # Windows ProactorEventLoop teardown noise, not a real failure: pytest-asyncio's
        # per-test loop can finish closing before asyncpg's connection-close callback
        # (scheduled via loop.create_task inside engine.dispose()) gets to run. The
        # test's own assertions have already completed by this point either way.
        #
        # THE SAME RACE HAS TWO FACES. Catching only RuntimeError left the other one
        # escaping as a teardown ERROR on a test that had already passed: once the loop
        # is gone asyncpg's transport is None, and the close path reaches
        # `self._transport.send(...)` -> AttributeError: 'NoneType' object has no
        # attribute 'send'. Same cause, different exception type.
        #
        # Both are matched on their MESSAGE, not just their type, so a genuine
        # AttributeError in teardown still fails loudly instead of being swallowed.
        try:
            await session.rollback()
            await session.close()
            await engine.dispose()
        except (RuntimeError, AttributeError) as exc:
            if not _is_loop_teardown_noise(exc):
                raise


@pytest_asyncio.fixture
async def redis_client(redis_url_str):
    import redis.asyncio as aioredis
    client = aioredis.from_url(redis_url_str)
    try:
        yield client
    finally:
        # Same Windows ProactorEventLoop teardown race as db_session above.
        try:
            await client.aclose()
        except (RuntimeError, AttributeError) as exc:
            if not _is_loop_teardown_noise(exc):
                raise


@pytest_asyncio.fixture
async def blob_client_fixture():
    if not AZURE_BLOB_ACCOUNT_URL:
        pytest.skip("AZURE_BLOB_ACCOUNT_URL not set")
    from shared.storage.azure_blob import BlobStorageClient
    client = BlobStorageClient()
    try:
        yield client
    finally:
        await client.close()
