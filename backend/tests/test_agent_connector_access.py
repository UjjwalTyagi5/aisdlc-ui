"""What an AGENT sees when its project may not do the thing it is about to try.

The wrapper enforces; this is about what comes back. A refusal that arrives as an
exception is caught by each tool's `except Exception` and returned as "Error creating
Feature 'X': …", which reads like the board rejected the item — and an LLM will
reasonably retry it. Retrying a permission denial never succeeds. So the tools refuse
up front, in the same shape as "no board is connected": a statement of what is not
possible and who can change it.
"""
import pytest

from config.connectors.scoped import ConnectorAccessDenied, ScopedConnector
from config.connectors.context import clear_connector, set_connector


class _Board:
    """A board connector that would succeed if it were ever reached."""

    connector_name = "jira"
    display_name = "Jira"

    def __init__(self):
        self.reached = []

    async def read_adapter(self, operation, **kw):
        self.reached.append(("read", operation))
        return [{"id": "1", "title": "A story"}]

    async def write_adapter(self, operation, **kw):
        self.reached.append(("write", operation))
        return {"work_item_id": "1", "url": "http://x/1"}

    def capability_manifest(self):
        from config.connectors.models import CapabilityEntry, CapabilityManifest
        return CapabilityManifest(
            connector_name="jira",
            read_capabilities={"list_stories": CapabilityEntry(status="implemented")},
            write_capabilities={"create_item": CapabilityEntry(status="implemented")},
            listen_capabilities={},
        )


@pytest.fixture
def board():
    inner = _Board()
    yield inner
    clear_connector()


def _inject(inner, level):
    set_connector(ScopedConnector(inner, level))


@pytest.mark.asyncio
async def test_a_read_only_project_refuses_a_write_tool_without_calling_the_board(board):
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "read")
    connector, err = await planning._board_connector("write")

    assert connector is None
    assert err and "read-only" in err
    # It must name the fix, not just the refusal.
    assert "Integrations page" in err
    # And nothing reached the board.
    assert board.reached == []


@pytest.mark.asyncio
async def test_a_read_only_project_still_reads(board):
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "read")
    connector, err = await planning._board_connector("read")
    assert err is None
    assert connector is not None


@pytest.mark.asyncio
async def test_a_write_only_project_refuses_a_read_tool(board):
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "write")
    connector, err = await planning._board_connector("read")
    assert connector is None
    assert "write-only" in err
    assert board.reached == []


@pytest.mark.asyncio
async def test_a_read_write_project_is_refused_nothing(board):
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "read_write")
    for mode in ("read", "write"):
        connector, err = await planning._board_connector(mode)
        assert err is None, f"{mode} was refused under read_write"


@pytest.mark.asyncio
async def test_an_ungranted_project_is_refused_both_ways(board):
    """`None` is no grant at all, and must behave exactly like an unknown level."""
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, None)
    for mode in ("read", "write"):
        connector, err = await planning._board_connector(mode)
        assert connector is None
        assert err
    assert board.reached == []


@pytest.mark.asyncio
async def test_an_unscoped_connector_is_not_second_guessed(board):
    """A bare connector — a test double, or an `unrestricted=True` path — has no
    access_level attribute and must pass through rather than being denied."""
    from agents_orchestrator.requirements_agent.agents import planning

    set_connector(board)  # not wrapped
    try:
        for mode in ("read", "write"):
            connector, err = await planning._board_connector(mode)
            assert err is None
            assert connector is board
    finally:
        clear_connector()


@pytest.mark.asyncio
async def test_the_wrapper_still_enforces_independently(board):
    """The pre-check is the courteous path, not the boundary. A tool that forgets to
    pass a mode must still be stopped."""
    scoped = ScopedConnector(board, "read")
    with pytest.raises(ConnectorAccessDenied):
        await scoped.write_adapter("create_item", title="x")
    assert board.reached == []
