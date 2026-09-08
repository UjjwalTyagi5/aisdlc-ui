"""The user's "yes" is recorded, so a confirmed action can actually happen.

REPORTED, twice, and the second time with the ownership fix already in. A Project
Admin asked Requirements to create work items on Azure Boards, was asked to confirm,
typed "yes" — and was asked again. And again. The agent eventually gave up and handed
out manual links:

    "I sincerely apologize for the frustration. The system continues to block the write
     operation despite your multiple approvals."

The backend log says exactly what happened, on every one of those turns:

    INFO - consequential action needs explicit approval (stage=requirements)

`authorize_consequential` has two halves. The first — does this person own the stage —
was fixed. The second asks `get_consequential_approved()`: did they say yes to THIS
action on THIS turn. It reads a contextvar that something has to set from the user's
message, and

    grep -rn "set_consequential_approved" --include=*.py .

finds `requirements_agent_api.py` and `design_architecture_agent_api.py` — the
standalone wrappers — and NOTHING in `orchestrator2`. So the flag was False on every
turn, no approval could ever register, and the gate looped forever.

The user's own diagnosis was the giveaway: "the standalone agent was able to create the
stories on azure". Same agent, same connector, same credentials; the only difference is
the wrapper around it.

THE SIXTH INSTANCE of one pattern on this branch — state the standalone `*_agent_api.py`
wrappers provide that `orchestrator2` skips, after the project connector, the MCP tools,
the per-run contextvars, `runs.development_artifacts`, and the tenant id.
"""
import pytest

from config import ws_helper


@pytest.fixture(autouse=True)
def _clean():
    ws_helper.set_consequential_approved(False)
    yield
    ws_helper.set_consequential_approved(False)


def test_the_turn_records_whether_the_user_approved():
    """The wiring. Without it the gate can never see a yes, whatever the user types."""
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    src = inspect.getsource(dispatch.run_agent)
    assert "set_consequential_approved(" in src, (
        "the turn never tells the consequential gate whether this message was an "
        "approval, so every confirmation is discarded and the gate loops"
    )
    assert "is_approval_message(" in src, (
        "approval is being decided by something other than the shared helper — the "
        "standalone path uses `is_approval_message`, and two different notions of "
        "'yes' is how one surface accepts what the other rejects"
    )


@pytest.mark.parametrize("text", [
    "yes",
    "Yes",
    "yes go ahead",
    "ok do it",
    "go ahead and create them",
    "approved",
])
def test_the_words_a_person_actually_types_count_as_approval(text):
    """The reported user typed a bare "yes". Earlier versions of this helper were so
    strict the product was unusable — a gate that cannot recognise "yes create these
    two for me" protects nothing and just invites its own removal."""
    from shared.authz.consequential import is_approval_message

    assert is_approval_message(text), f"{text!r} was not read as approval"


@pytest.mark.parametrize("text", [
    "no",
    "not yet",
    "who needs to approve this?",
    "don't create them",
    "what happens when I approve it?",
])
def test_a_refusal_or_a_question_is_never_approval(text):
    """The other direction, and the reason this is not just `"yes" in text`."""
    from shared.authz.consequential import is_approval_message

    assert not is_approval_message(text), f"{text!r} was read as approval"


@pytest.mark.asyncio
async def test_a_turn_that_is_not_an_approval_clears_the_previous_yes(monkeypatch):
    """Consent is for ONE action. "Yes, create those work items" is not standing
    permission to create more on the next message, so a non-approval turn must reset
    the flag rather than inherit it."""
    from agents_orchestrator.orchestrator2 import dispatch
    from shared.authz.consequential import is_approval_message

    # The property, stated over the helper the turn uses: an ordinary instruction is
    # not consent, so recording it clears whatever the previous turn set.
    ws_helper.set_consequential_approved(True)
    ws_helper.set_consequential_approved(is_approval_message("and now add a third one"))
    assert ws_helper.get_consequential_approved() is False

    import inspect
    src = inspect.getsource(dispatch.run_agent)
    assert "set_consequential_approved(is_approval_message(text))" in src, (
        "the turn must record the CURRENT message's verdict unconditionally — setting "
        "it only when the message is an approval would let one yes stand for the rest "
        "of the conversation"
    )


@pytest.mark.asyncio
async def test_end_to_end_a_project_admin_who_says_yes_gets_through(monkeypatch):
    """Both halves together, which is what the user was actually trying to do."""
    from shared.authz import consequential
    from shared.authz.consequential import is_approval_message

    async def _project_admin(user_id, tenant_id):
        from shared.authz.permissions import _ROLE_PERMISSIONS
        return list(_ROLE_PERMISSIONS["project_admin"])

    monkeypatch.setattr(
        "shared.authz.resolver.resolve_permissions_for_user", _project_admin
    )
    ws_helper.set_user_id("u1")
    ws_helper.set_tenant_id("t1")
    ws_helper.set_orchestrator_run(True)
    ws_helper.set_consequential_approved(is_approval_message("yes"))

    ok, why = await consequential.authorize_consequential(
        "requirements", action="Writing to the project board",
    )
    assert ok, f"a Project Admin who typed 'yes' was still refused: {why}"


@pytest.mark.asyncio
async def test_without_a_yes_it_still_asks(monkeypatch):
    """The gate has to keep doing its job. Recording consent is not removing it."""
    from shared.authz import consequential
    from shared.authz.consequential import is_approval_message

    async def _project_admin(user_id, tenant_id):
        from shared.authz.permissions import _ROLE_PERMISSIONS
        return list(_ROLE_PERMISSIONS["project_admin"])

    monkeypatch.setattr(
        "shared.authz.resolver.resolve_permissions_for_user", _project_admin
    )
    ws_helper.set_user_id("u1")
    ws_helper.set_tenant_id("t1")
    ws_helper.set_orchestrator_run(True)
    ws_helper.set_consequential_approved(
        is_approval_message("create a work item called test")
    )

    ok, _ = await consequential.authorize_consequential(
        "requirements", action="Writing to the project board",
    )
    assert not ok, "the board was written to without the user confirming"



def test_consent_does_not_outlive_the_turn():
    """Found by mutation. Within the Orchestrator a stale True is harmless — the next
    turn overwrites it from that message. It is the OTHER callers on this worker that
    matter: a standalone agent request, or a background job, runs with this contextvar
    untouched and would inherit a yes it was never given.

    Same leak class as the stage-ownership exemption, and cleared in the same place.
    """
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    src = inspect.getsource(dispatch.run_agent)
    assert "set_consequential_approved(False)" in src, (
        "the turn never clears consent, so a yes leaks to whatever runs next on this "
        "worker"
    )
