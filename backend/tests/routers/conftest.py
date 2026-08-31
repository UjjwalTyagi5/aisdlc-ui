"""Test fixtures for tests/routers.

Session-scoped event loop so all async tests in this package share
the same loop — prevents "Event loop is closed" errors from the shared
SQLAlchemy async engine pool being reused across function-scoped loops.
"""
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop shared by all tests in this package."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
