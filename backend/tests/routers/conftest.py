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


@pytest.fixture(autouse=True)
def _patch_authz_for_unit_tests(monkeypatch, request):
    """Patch authorization checks to allow unit tests with mocked DB.

    Unit tests with @pytest.mark.unit use httpx ASGI transport (full middleware)
    with mocked get_db_session. Authorization dependencies run but can't query
    real DB, so they deny access. This patch allows the checks to pass for unit tests
    while preserving real checks for integration tests.
    """
    if "unit" not in [mark.name for mark in request.node.iter_markers()]:
        # Only patch for unit tests - integration tests need real auth checks
        return

    from shared.authz.project_scope import assert_can_read_project
    from shared.authz.agent_access import assert_agent_access

    # Store originals
    original_assert_can_read = assert_can_read_project
    original_assert_agent = assert_agent_access

    # Create pass-through mocks that always succeed
    async def _mock_assert_can_read(db, request, project_id: str):
        class MockProject:
            id = project_id
        return MockProject()

    async def _mock_assert_agent_access(*args, **kwargs):
        # Allow all agent access in unit tests
        return

    # Patch the functions
    monkeypatch.setattr(
        "shared.authz.project_scope.assert_can_read_project",
        _mock_assert_can_read,
    )
    monkeypatch.setattr(
        "shared.authz.agent_access.assert_agent_access",
        _mock_assert_agent_access,
    )
