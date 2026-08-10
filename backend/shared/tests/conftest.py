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


@pytest_asyncio.fixture
async def db_session(pg_conn_string):
    # NullPool prevents connection pooling between tests — each test gets a fresh connection
    engine = create_async_engine(pg_conn_string, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def redis_client(redis_url_str):
    import redis.asyncio as aioredis
    client = aioredis.from_url(redis_url_str)
    try:
        yield client
    finally:
        await client.aclose()


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
