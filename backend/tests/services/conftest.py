"""Test fixtures for tests/services.

Provides a session-scoped event loop so all async tests in this package share the
same loop — required to avoid "Event loop is closed" errors when a module-level
SQLAlchemy async engine is reused across multiple test functions.
"""
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop — shared by all tests in this package."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()