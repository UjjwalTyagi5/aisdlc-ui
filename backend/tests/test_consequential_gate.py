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

    with patch.object(planning, "_authorize_consequential",
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
    with patch.object(planning, "_authorize_consequential", approver), \
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
        # Prefix, not the whole call: the choke point also takes an optional `provider`
        # now, so pinning the exact string made this fail on a change that did not
        # weaken anything. What must stay true is that write tools ask for "write"
        # MODE — that is the argument the tier and lattice checks hang off.
        assert '_board_connector("write"' in chunk, (
            f"{name} calls write_adapter without going through the gated choke point"
        )


@pytest.mark.unit
def test_the_design_epic_write_is_gated():
    src = _design_epic_tool_source()
    assert "authorize_consequential(" in src
    # The gate runs BEFORE the write, not as an afterthought beside it.
    assert src.index("authorize_consequential(") < src.index("write_adapter")


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


# ── the second half: the owner has to actually say yes, on this turn ─────────
#
# WHY THIS EXISTS AT ALL. `owner_approved` asks whether this PERSON may authorise the
# action. On its own it is not a gate here, and the reason is arithmetic rather than
# subtle: the owning role for `requirements` is `ba`, so a BA driving the chat holds
# `artifact:approve_requirements` by definition and passes the role check on every
# turn. Every board write a BA's session produced would have gone through with nobody
# asked — including `delete_board_item`, which calls itself IRREVERSIBLE. The
# Development agent has the mirror-image hole: push_gate_enabled/push_approved asks the
# human but never checks the role, so any project member driving that chat can approve
# a push. These tests pin both halves running together.


def _consent(approved: bool):
    import config.ws_helper as ws

    return patch.object(ws, "get_consequential_approved", lambda: approved)


async def _ask_full(stage, *, approved, user_id=OWNER, perms=BA_PERMS):
    from shared.authz.consequential import authorize_consequential

    async def _resolve(_u, _t):
        return perms

    ctx_user, ctx_tenant = _context(user_id)
    with ctx_user, ctx_tenant, _consent(approved), \
            patch("shared.authz.resolver.resolve_permissions_for_user", _resolve):
        return await authorize_consequential(stage, action="Writing to the board")


@pytest.mark.unit
async def test_the_owner_alone_is_not_enough_without_an_explicit_yes():
    ok, why = await _ask_full("requirements", approved=False)
    assert ok is False
    assert "NOT DONE" in why


@pytest.mark.unit
async def test_the_owner_plus_an_explicit_yes_passes():
    ok, why = await _ask_full("requirements", approved=True)
    assert ok is True and why == ""


@pytest.mark.unit
async def test_a_yes_from_a_non_owner_is_still_refused():
    """Consent does not substitute for authority. Somebody without the stage's approval
    permission saying "yes" is not an approval — their yes was never theirs to give."""
    ok, why = await _ask_full("requirements", approved=True, user_id=BYSTANDER, perms=NO_PERMS)
    assert ok is False
    assert "NOT DONE" not in why  # they hear about authority, not about being asked


@pytest.mark.unit
async def test_the_role_check_is_reported_before_the_consent_one():
    """Order matters for the message, not just the outcome. Telling somebody who lacks
    the permission to "ask the user for approval" is nonsense — they ARE the user, and
    their yes would not count."""
    ok, why = await _ask_full("design", approved=False, user_id=BYSTANDER, perms=NO_PERMS)
    assert ok is False
    assert "approval permission" in why


@pytest.mark.unit
async def test_a_background_run_has_no_consent_to_inherit():
    """A worker sets no user and no consent flag. Both halves refuse, and the one that
    answers first is the one naming the real problem: nobody is there."""
    ok, why = await _ask_full("requirements", approved=False, user_id=None)
    assert ok is False
    assert "signed-in approver" in why


# ── what counts as a yes ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "yes", "Yes", "  ok  ", "confirm", "proceed", "go ahead", "approved",
    "yes please", "I approve", "please proceed", "you have my approval",
    # A leading approval clause, and a phrase the message opens with.
    "yes, go ahead", "Yes, create them", "go ahead and create them",
    # FROM A REAL TRANSCRIPT. Every one of these was REJECTED by the first version of
    # this rule, which demanded the whole message be an approval word. The user
    # approved four times, was refused four times, and the agent re-asked each time.
    # A gate that cannot read "yes create these two for me" protects nothing — it
    # makes the feature unusable, and the pressure that creates is to delete the gate.
    "yes create these two for me",
    "yes do this",
    "ok do it",
    "approve this",
    "sure, create both of them",
    "yep go ahead and create them",
    "do it",          # must survive the "do not" refusal rule
    "do it now",
])
def test_these_are_approvals(text):
    from shared.authz.consequential import is_approval_message

    assert is_approval_message(text) is True


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "",
    None,
    "no",
    "do not approve that",
    "who needs to approve this?",
    "Should I approve this?",
    # Contains "i approve" but does not open with it. An unanchored substring test
    # read this as consent, which is why the matching is anchored.
    "what happens when I approve it",
    "no, do not go ahead",
    "I am not sure, do not proceed yet",
    "write the stories to the board",
    "delete the epic",
    # The Development agent treats these as approvals because its action is always a
    # push. On a board they describe work rather than consent to it, which is why the
    # two phrase lists are deliberately not shared.
    "push the release notes to the epic",
    "create the pr description as a work item",
    # LEADING REFUSALS. This is why leading-token matching is safe at all: the refusal
    # check runs FIRST, so a message opening with one never reaches the yes rules
    # however many approving words appear later in it.
    "nope, cancel that",
    "don't approve that",
    "dont create them",
    "stop, go ahead is not what I said",
    "cancel it",
    "wait, approve only the first one",
    "not yet, proceed tomorrow",
    # A bare INSTRUCTION is not an approval. Treating it as one would defeat the
    # propose-then-approve shape entirely: the model could act on the first message
    # without ever having said what it was about to do.
    "create them",
    "create it",
    "create both stories on the board",
])
def test_these_are_not_approvals(text):
    from shared.authz.consequential import is_approval_message

    assert is_approval_message(text) is False


@pytest.mark.unit
def test_a_transcript_approval_does_not_authorise_the_next_turn():
    """The chat entry points pass ONLY this turn's `task_intent`, never the replayed
    `conversation_context`. If they ever passed both, an approval anywhere in the
    history would authorise every later write in the session."""
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as dapi
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    for mod in (api, dapi):
        src = inspect.getsource(mod)
        assert "set_consequential_approved(is_approval_message(task_intent))" in src, mod.__name__
        assert "is_approval_message(conversation_context" not in src, mod.__name__


@pytest.mark.unit
def test_consent_is_set_on_every_chat_turn_of_both_agents():
    """Both agents, both paths (WS and REST) = two call sites each. Set
    UNCONDITIONALLY: a turn that is not an approval has to clear the previous turn's
    yes, or one "yes" becomes standing permission for the rest of the session."""
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as dapi
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    for mod in (api, dapi):
        src = inspect.getsource(mod)
        assert src.count("set_consequential_approved(") == 2, mod.__name__


# ── the consent flag actually reaches the tools ──────────────────────────────


@pytest.mark.unit
def test_consent_set_in_the_outer_frame_reaches_a_tool():
    """THE ASSUMPTION THE WHOLE GATE RESTS ON, and it is not obvious.

    Requirements' `action` node is SYNC and runs its tools through
    `asyncio.run(execute_tools())` — a fresh event loop. Its own comments record that
    `set_resolved_model()`, called inside the async `agent` node, does NOT survive into
    that context; the resolved model has to be threaded through graph state instead.

    Consent survives where the model does not, and the difference is WHERE it is set,
    not what it is. `asyncio.run` copies the CURRENT context, so a value set in an
    ancestor frame — the WS/REST handler, before the graph is invoked — is visible;
    a value set inside a sibling task is not. Setting the flag in the handler rather
    than in a node is therefore load-bearing, and this test fails if anyone moves it.

    If this ever broke, `get_consequential_approved()` would read False inside every
    tool and every board write would refuse — the feature would look dead rather than
    broken, which is exactly the failure mode this branch has been fixing elsewhere.
    """
    import asyncio

    from config.ws_helper import get_consequential_approved, set_consequential_approved

    def action_node():
        """The shape of planning.action → execute_tools."""
        async def execute_tools():
            return get_consequential_approved()

        return asyncio.run(execute_tools())

    set_consequential_approved(True)
    assert action_node() is True
    set_consequential_approved(False)
    assert action_node() is False


@pytest.mark.unit
def test_consent_set_inside_a_node_would_not_reach_the_caller():
    """The control for the test above: this is the propagation that does NOT work, and
    is why the flag is set in the handler. Pinned so the distinction stays visible."""
    import asyncio

    from config.ws_helper import get_consequential_approved, set_consequential_approved

    async def sets_it_inside_a_task():
        set_consequential_approved(True)

    async def outer():
        set_consequential_approved(False)
        await asyncio.create_task(sets_it_inside_a_task())
        return get_consequential_approved()

    assert asyncio.run(outer()) is False
