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

#: Opt out of the throwaway database and destroy the environment's own, for
#: reproducing a migration failure against a specific database. Deliberately awkward
#: to type by accident and greppable when somebody wonders how it got set.
_OPT_IN = "ALLOW_DESTRUCTIVE_DB_TESTS"


def _database_name(dsn: str) -> str:
    """The database name out of a SQLAlchemy/libpq URL, minus any query string."""
    m = re.search(r"/([^/?]+)(?:\?|$)", dsn or "")
    return m.group(1) if m else ""


def _maintenance_dsn(dsn: str) -> str:
    """The same server, as libpq, pointed at `postgres` — where CREATE DATABASE runs."""
    without_driver = re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)
    return re.sub(r"/([^/?]+)(\?|$)", r"/postgres\2", without_driver)


@pytest.fixture
def disposable_migrations_dsn():
    """A database of this test's own, created here and dropped afterwards.

    WHY THIS EXISTS. `test_alembic_migration_cycle` runs `alembic downgrade base`,
    which drops the entire schema. It took the DSN straight from the environment and
    the only guard was "is it set", so pointing it at a development database -- which
    is the normal thing for POSTGRES_MIGRATIONS_CONN_STRING to hold -- silently
    destroyed it. That happened on 2026-09-13: one organization, one business unit,
    one project, the Langfuse bindings and 125 audit events, gone in a run that
    reported itself as a single ordinary test failure.

    A NAME WAS NOT ENOUGH. The first fix accepted any database whose name looked
    disposable, which in a full suite run means `sdlc_product_test` -- the database
    every other test is using at that moment. The cycle then dropped all 60-odd tables
    under ~5000 running tests: foreign-key violations in their cleanup fixtures, reads
    that returned nothing, and this test failing on its own `downgrade base` against
    the locks they held. A test that drops every table cannot share a database with
    anything, however well named.

    So it gets one. Created on the same server with the same credentials, migrated,
    destroyed, and never touched by another test. `ALLOW_DESTRUCTIVE_DB_TESTS=1` still
    means what it said -- use the environment's own database -- for the rare case of
    reproducing a migration failure against a specific one.
    """
    dsn = POSTGRES_MIGRATIONS_CONN_STRING
    if not dsn:
        pytest.skip("POSTGRES_MIGRATIONS_CONN_STRING not set")

    if os.environ.get(_OPT_IN) == "1":
        return dsn

    import uuid as _uuid

    try:
        import psycopg
    except ImportError:  # pragma: no cover — psycopg ships with the app
        pytest.skip("psycopg is needed to create the throwaway migration database")

    name = f"sdlc_migration_cycle_{_uuid.uuid4().hex[:12]}"
    admin = _maintenance_dsn(dsn)
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=10) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # noqa: BLE001 — no CREATEDB right is a skip, not a failure
        pytest.skip(
            f"cannot create a throwaway database for the migration cycle "
            f"({type(exc).__name__}: {exc}). The role needs CREATEDB, or set "
            f"{_OPT_IN}=1 to run the cycle against "
            f"{_database_name(dsn)!r} itself -- which DROPS EVERY TABLE in it."
        )

    try:
        yield re.sub(r"/([^/?]+)(\?|$)", rf"/{name}\2", dsn)
    finally:
        try:
            with psycopg.connect(admin, autocommit=True, connect_timeout=10) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        except Exception:  # noqa: BLE001 — a leftover scratch database is not a failure
            pass


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
