"""Consequential agent actions need an owner, enforced in code rather than in a prompt.

WHAT THIS REPLACES. Before this, the only thing between the model and a live board was
a sentence in a tool docstring — `update_ado_epic_design_complete` said "Always confirm
with the user before calling this tool". The status doc records the same arrangement for
Documentation's `open_docs_pr` and `publish_to_sharepoint` and names it for what it is:
*"enforced by prompt text only"*, the tool node executes whatever the model emits. A
prompt is a request. These tests are the control.

THE CHOKE POINT MATTERS. Requirements has about twenty board tools and nine of them
write. The gate is not on each tool — it is on `_board_connector("write")`, which every
one of them already goes through, so a write tool added next month is covered without
anybody remembering to cover it. `test_every_write_tool_goes_through_the_choke_point`
is what keeps that true.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TENANT = "11111111-1111-1111-1111-111111111111"
OWNER = "the-ba"
BYSTANDER = "a-developer"

BA_PERMS = ["artifact:approve_requirements"]
ARCHITECT_PERMS = ["artifact:approve_design"]
NO_PERMS = ["artifact:view"]


def _context(user_id: str | None, tenant_id: str | None = TENANT):
    """Patch the session context the rule reads its actor from."""
    import config.ws_helper as ws

    return (
        patch.object(ws, "get_user_id", lambda: user_id),
        patch.object(ws, "get_tenant_id", lambda: tenant_id),
    )


async def _ask(stage, *, user_id=OWNER, perms=BA_PERMS, resolver_raises=False):
    from shared.authz.consequential import owner_approved

    async def _resolve(_u, _t):
        if resolver_raises:
            raise RuntimeError("directory unreachable")
        return perms

    ctx_user, ctx_tenant = _context(user_id)
    with ctx_user, ctx_tenant, \
            patch("shared.authz.resolver.resolve_permissions_for_user", _resolve):
        return await owner_approved(stage)


# ── the rule ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_the_owning_role_may_authorise_it():
    ok, why = await _ask("requirements", perms=BA_PERMS)
    assert ok is True and why == ""


@pytest.mark.unit
async def test_somebody_without_the_approval_permission_may_not():
    """Holding the connector's write access is not the same as being allowed to
    authorise the action — this asks about the PERSON, not the project."""
    ok, why = await _ask("requirements", perms=NO_PERMS)
    assert ok is False
    assert "business analyst" in why.lower()
    assert "consequential" in why.lower()


@pytest.mark.unit
async def test_each_stage_names_its_own_owner():
    """requirements → ba, design → architect. A single generic "an approver" message
    would send half the readers to the wrong person."""
    _, req_why = await _ask("requirements", perms=NO_PERMS)
    _, des_why = await _ask("design", perms=NO_PERMS)
    assert "business analyst" in req_why.lower()
    assert "architect" in des_why.lower()


@pytest.mark.unit
async def test_the_design_owner_cannot_authorise_a_requirements_action():
    """The permissions are per stage. An architect holds approve_design and that must
    not carry over to the Requirements board."""
    ok, _ = await _ask("requirements", perms=ARCHITECT_PERMS)
    assert ok is False
    ok, _ = await _ask("design", perms=ARCHITECT_PERMS)
    assert ok is True


@pytest.mark.unit
async def test_an_admin_wildcard_still_works():
    """can_user_approve handles `admin:*`; the rule must inherit that rather than
    re-implement permission matching."""
    ok, _ = await _ask("requirements", perms=["admin:*"])
    assert ok is True


# ── it fails closed, on every uncertainty ────────────────────────────────────


@pytest.mark.unit
async def test_no_signed_in_user_is_a_refusal():
    """A background worker run sets no user. A Consequential action with nobody
    accountable is exactly what the tier exists to prevent, so unknown is no."""
    ok, why = await _ask("requirements", user_id=None)
    assert ok is False
    assert "background" in why.lower()


@pytest.mark.unit
async def test_an_unresolvable_permission_set_is_not_an_empty_one():
    """A directory outage must not read as "this user has no objection"."""
    ok, why = await _ask("requirements", resolver_raises=True)
    assert ok is False
    assert "could not be checked" in why.lower()


@pytest.mark.unit
async def test_the_refusal_never_leaks_the_underlying_error():
    _, why = await _ask("requirements", resolver_raises=True)
    assert "directory unreachable" not in why
    assert "RuntimeError" not in why


# ── it is wired in, at the choke point ───────────────────────────────────────


@pytest.mark.unit
async def test_the_requirements_board_write_is_gated():
    """`_board_connector("write")` is what every Requirements write tool calls."""
    from agents_orchestrator.requirements_agent.agents import planning

    with patch.object(planning, "_owner_approved",
                      AsyncMock(return_value=(False, "no owner present"))), \
            patch.object(planning, "_get_active_connector", lambda: _AnyConnector()):
        connector, err = await planning._board_connector("write")
    assert connector is None
    assert err == "no owner present"


@pytest.mark.unit
async def test_reads_are_not_gated():
    """Reading the board is Safe (§1.5) — gating it would make the agent useless to
    everyone who is not an approver, for no protection at all."""
    from agents_orchestrator.requirements_agent.agents import planning

    approver = AsyncMock(return_value=(False, "no owner present"))
    with patch.object(planning, "_owner_approved", approver), \
            patch.object(planning, "_get_active_connector", lambda: _AnyConnector()):
        connector, err = await planning._board_connector("read")
    assert err is None and connector is not None
    approver.assert_not_awaited()


@pytest.mark.unit
def test_every_write_tool_goes_through_the_choke_point():
    """The gate is on `_board_connector("write")`, not on each tool. A write tool that
    calls `write_adapter` without having asked for a "write" connector would bypass it
    entirely — this is the test that notices."""
    import inspect

    from agents_orchestrator.requirements_agent.agents import planning

    src = inspect.getsource(planning)
    # Every function body that writes must also request the write-mode connector.
    for chunk in src.split("\n@tool\n")[1:]:
        if "write_adapter(" not in chunk:
            continue
        name = chunk.split("async def ", 1)[1].split("(", 1)[0]
        assert '_board_connector("write")' in chunk, (
            f"{name} calls write_adapter without going through the gated choke point"
        )


@pytest.mark.unit
def test_the_design_epic_write_is_gated():
    src = _design_epic_tool_source()
    assert 'owner_approved("design")' in src
    # The gate runs BEFORE the write, not as an afterthought beside it.
    assert src.index('owner_approved("design")') < src.index("write_adapter")


@pytest.mark.unit
def test_the_design_epic_write_does_not_leak_exception_text():
    """It used to return `f"Warning: Could not move epic state: {exc}"` — a connector
    error carries the board's instance URL and full API path, and that string lands in
    the model's context and the saved transcript."""
    src = _design_epic_tool_source()
    assert "{exc}" not in src
    assert "type(exc).__name__" in src


def _design_epic_tool_source() -> str:
    """The body of `update_ado_epic_design_complete`, read from the file.

    `inspect.getsource` cannot reach it: @tool wraps an async function in a
    StructuredTool whose `.func` is None (the callable lives on `.coroutine`), and the
    wrapper itself has no source file. Slicing the module text is the reliable way to
    assert on what the tool actually does.
    """
    from agents_orchestrator.design_architecture_agent.agents import architecture

    text = Path(architecture.__file__).read_text(encoding="utf-8")
    start = text.index("async def update_ado_epic_design_complete(")
    end = text.index("@tool", start)
    return text[start:end]


class _AnyConnector:
    """A bare connector: no `access_level`, so `_board_connector` does not second-guess
    the level and we are testing only the tier gate."""

    display_name = "Test Board"
    connector_name = "test_board"
