"""What an AGENT sees when its project may not do the thing it is about to try.

The wrapper enforces; this is about what comes back. A refusal that arrives as an
exception is caught by each tool's `except Exception` and returned as "Error creating
Feature 'X': …", which reads like the board rejected the item — and an LLM will
reasonably retry it. Retrying a permission denial never succeeds. So the tools refuse
up front, in the same shape as "no board is connected": a statement of what is not
possible and who can change it.
"""
import contextlib
from unittest.mock import patch

import pytest

from config.connectors.scoped import ConnectorAccessDenied, ScopedConnector
from config.connectors.context import clear_connector, set_connector


@contextlib.contextmanager
def as_owner():
    """Put an owner of the Requirements stage in session context.

    `_board_connector("write")` now enforces TWO independent things, and these tests
    are about the first:

        permits(level, "write")   may this PROJECT'S STAGE write to its board
        owner_approved(stage)     may this PERSON authorise a Consequential action

    A test that establishes no person hits the second check and never reaches the
    lattice question it was written to ask. This states the precondition those tests
    always implicitly assumed — a human driving the turn — so each layer is tested on
    its own. `test_a_write_needs_an_owner_even_on_a_read_write_project` below covers
    the interaction itself.
    """
    import config.ws_helper as ws

    async def _perms(_u, _t):
        return ["artifact:approve_requirements"]

    with patch.object(ws, "get_user_id", lambda: "the-ba"),             patch.object(ws, "get_tenant_id", lambda: "tenant-1"),             patch("shared.authz.resolver.resolve_permissions_for_user", _perms):
        yield


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
    with as_owner():
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
        with as_owner():
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


# ── the two layers are independent, and both bind ────────────────────────────


@pytest.mark.asyncio
async def test_a_write_needs_an_owner_even_on_a_read_write_project(board):
    """The project's grant is not a person's authority.

    `read_write` says this stage MAY write to its board. It does not say that whoever
    is driving this turn may authorise the write — creating or deleting work items is
    Consequential (§1.5) and needs the owning role. Both checks bind; neither implies
    the other.
    """
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "read_write")
    connector, err = await planning._board_connector("write")
    assert connector is None
    assert err and "approver" in err
    assert board.reached == [], "nothing may reach the board without an approver"


@pytest.mark.asyncio
async def test_reading_needs_no_owner(board):
    """Reading the board is Safe. Requiring an approver to read would lock the agent
    out for everyone who is not one, and protect nothing."""
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "read")
    connector, err = await planning._board_connector("read")
    assert err is None
    assert connector is not None


@pytest.mark.asyncio
async def test_the_level_is_checked_before_the_approver(board):
    """Order matters for the message. A project with no write grant should hear about
    the grant — which an admin fixes on the Integrations page — rather than be told to
    find an approver for a write that could not happen either way."""
    from agents_orchestrator.requirements_agent.agents import planning

    _inject(board, "read")
    connector, err = await planning._board_connector("write")
    assert connector is None
    assert "read-only" in err
    assert "approver" not in err
