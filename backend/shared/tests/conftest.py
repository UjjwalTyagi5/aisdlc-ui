import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from config.env import POSTGRES_CONN_STRING, REDIS_URL, AZURE_BLOB_ACCOUNT_URL


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
