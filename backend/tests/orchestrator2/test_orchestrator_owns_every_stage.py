"""In the Orchestrator, the Project Admin owns every stage.

REPORTED. A Project Admin drove Development through a colour change, asked Requirements
to create the matching stories on Azure Boards, confirmed with "yes", and got:

    "I cannot create work items on Azure Boards in the current context because this
     action requires an approver to be signed in.
     From the project chat — Open the project in the main chat interface where you're
     signed in, and ask me to create these work items."

Two separate faults, and the first hid the second.

1. NO ACTOR. `owner_approved` reads `get_user_id()` AND `get_tenant_id()` from
   `config.ws_helper`, and refuses with `_NO_ACTOR` if either is empty. `dispatch.py`
   set the user and never the tenant, so every Orchestrator run looked like an
   unattended background job. That is the fifth instance of one pattern on this branch:
   state the standalone `*_agent_api.py` wrappers provide that `orchestrator2` skips,
   after the connector, the MCP tools, the per-run contextvars and
   `runs.development_artifacts`.

2. NOT OWNER. Underneath it, a real wall: a `project_admin` holds
   `artifact:approve_documentation` and `artifact:approve_plan` and nothing else, so
   `can_user_approve` says False for the other seven — requirements included. Fixing
   the tenant alone would have moved the refusal from "who are you" to "you may not",
   which is the same dead end one message further along.

`orchestrator_instruction.md` §1.5 settled this before either was written:

    "Only a Project Admin can use the Orchestrator, because only that role has access
     to all agents." … "There is no sign-off and no gate in the Orchestrator, because
     the Project Admin running it already has access to every agent."

WHAT IS NOT REMOVED: the consent half. `get_consequential_approved()` asks whether the
user said yes to THIS action on THIS turn, which is not a permission — it is what makes
"yes" mean something before work items appear on somebody's real board. The user's own
transcript shows them saying "yes"; they were refused by the OWNER half before consent
was ever consulted. Removing that too would have the Orchestrator write to Azure DevOps
with no confirmation at all, which is not what "no permission issues" asks for.
"""
import pytest

from config import ws_helper


@pytest.fixture(autouse=True)
def _clean_context():
    """Contextvars leak between tests otherwise, and a leaked exemption is the one
    failure this file must never introduce."""
    ws_helper.set_user_id("")
    ws_helper.set_tenant_id(None)
    ws_helper.set_orchestrator_run(False)
    yield
    ws_helper.set_user_id("")
    ws_helper.set_tenant_id(None)
    ws_helper.set_orchestrator_run(False)


# ── 1. the actor reaches the gate at all ─────────────────────────────────────


def test_dispatch_sets_the_tenant_as_well_as_the_user():
    """`owner_approved` needs BOTH. With the tenant missing every Orchestrator run
    read as an unattended background job."""
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    src = inspect.getsource(dispatch.run_agent)
    assert "set_user_id(user_id)" in src
    assert "set_tenant_id(tenant_id)" in src, (
        "the turn never tells the consequential gate which tenant is acting, so it "
        "refuses with _NO_ACTOR before any permission is considered"
    )


@pytest.mark.asyncio
async def test_without_an_actor_the_gate_still_refuses():
    """The exemption must not become a way to act with nobody identified. A
    Consequential action with no human is exactly what the tier exists to prevent."""
    from shared.authz.consequential import owner_approved

    ws_helper.set_orchestrator_run(True)          # even inside the Orchestrator
    ok, why = await owner_approved("requirements")
    assert not ok
    assert "signed-in approver" in why


# ── 2. inside the Orchestrator, the Project Admin owns every stage ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
])
async def test_a_project_admin_owns_every_stage_in_an_orchestrator_run(
    stage, monkeypatch,
):
    """The reported case, for all of them — `requirements` is the one that was hit."""
    from shared.authz import consequential

    async def _project_admin(user_id, tenant_id):
        from shared.authz.permissions import _ROLE_PERMISSIONS
        return list(_ROLE_PERMISSIONS["project_admin"])

    monkeypatch.setattr(
        "shared.authz.resolver.resolve_permissions_for_user", _project_admin
    )
    ws_helper.set_user_id("u1")
    ws_helper.set_tenant_id("t1")
    ws_helper.set_orchestrator_run(True)

    ok, why = await consequential.owner_approved(stage)
    assert ok, f"a Project Admin was refused ownership of {stage}: {why}"


@pytest.mark.asyncio
async def test_outside_the_orchestrator_the_stage_owner_still_decides(monkeypatch):
    """The standalone agents keep their gates. This exemption is the Orchestrator's,
    granted because the socket has already verified the caller administers the run's
    project — a claim nothing else can make."""
    from shared.authz import consequential

    async def _project_admin(user_id, tenant_id):
        from shared.authz.permissions import _ROLE_PERMISSIONS
        return list(_ROLE_PERMISSIONS["project_admin"])

    monkeypatch.setattr(
        "shared.authz.resolver.resolve_permissions_for_user", _project_admin
    )
    ws_helper.set_user_id("u1")
    ws_helper.set_tenant_id("t1")
    ws_helper.set_orchestrator_run(False)

    ok, why = await consequential.owner_approved("requirements")
    assert not ok, "the standalone Requirements gate was opened for a non-owner"
    assert "approval permission" in why


@pytest.mark.asyncio
async def test_the_exemption_does_not_survive_the_turn():
    """A contextvar left set would carry the exemption into whatever ran next on the
    same worker — including a standalone agent's request."""
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    src = inspect.getsource(dispatch.run_agent)
    assert "set_orchestrator_run(True)" in src
    assert "set_orchestrator_run(False)" in src, (
        "the Orchestrator exemption is never cleared, so it leaks to the next run"
    )


# ── 3. consent is still required ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_orchestrator_still_asks_before_it_writes(monkeypatch):
    """Ownership is settled; consent is not. Creating work items on somebody's real
    Azure board without a yes is not what "no permission issues" asked for."""
    from shared.authz import consequential

    async def _project_admin(user_id, tenant_id):
        from shared.authz.permissions import _ROLE_PERMISSIONS
        return list(_ROLE_PERMISSIONS["project_admin"])

    monkeypatch.setattr(
        "shared.authz.resolver.resolve_permissions_for_user", _project_admin
    )
    ws_helper.set_user_id("u1")
    ws_helper.set_tenant_id("t1")
    ws_helper.set_orchestrator_run(True)
    ws_helper.set_consequential_approved(False)

    ok, why = await consequential.authorize_consequential(
        "requirements", action="Writing to the project board",
    )
    assert not ok
    assert "approval" in why.lower(), "the user was not asked to confirm"


@pytest.mark.asyncio
async def test_a_confirmed_action_goes_through(monkeypatch):
    """The other half — and the flow the user actually performed. They said "yes" and
    were still refused, because the owner check ran first and never reached this."""
    from shared.authz import consequential

    async def _project_admin(user_id, tenant_id):
        from shared.authz.permissions import _ROLE_PERMISSIONS
        return list(_ROLE_PERMISSIONS["project_admin"])

    monkeypatch.setattr(
        "shared.authz.resolver.resolve_permissions_for_user", _project_admin
    )
    ws_helper.set_user_id("u1")
    ws_helper.set_tenant_id("t1")
    ws_helper.set_orchestrator_run(True)
    ws_helper.set_consequential_approved(True)

    ok, why = await consequential.authorize_consequential(
        "requirements", action="Writing to the project board",
    )
    assert ok, f"a Project Admin who confirmed was still refused: {why}"



def test_the_exemption_is_off_unless_something_turns_it_on():
    """THE SAFETY PROPERTY, and the one every other test here skips.

    Found by mutation: flipping the contextvar's default to True broke nothing,
    because the fixture in this file sets it explicitly on every path. A default of
    True would exempt every standalone agent — each one runs with this contextvar
    untouched, so the stage owner would stop deciding anywhere at all.

    Read from a FRESH context, not from the ambient one this process has been
    mutating.
    """
    import contextvars

    from config.ws_helper import get_orchestrator_run

    assert contextvars.Context().run(get_orchestrator_run) is False, (
        "a turn that never identifies itself as the Orchestrator's is exempt by "
        "default, which hands every standalone agent the exemption too"
    )
