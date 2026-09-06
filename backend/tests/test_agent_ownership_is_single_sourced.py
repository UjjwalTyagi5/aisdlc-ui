"""Every agent has one owner, and that owner can actually approve its stage.

THE BUG THIS CATCHES, which was live in the product.

    AGENT_OWNER_ROLE = {"review": "architect", ...}       # keyed on the UI name
    _owner_label(stage)  ->  agent_owner_role("code_review")   # the BACKEND name

The miss fell through to `AGENT_OWNER_ROLE.get(phase, "project_admin")`, so the
platform told users a Project Admin had to sign off a Code Review consequential
action. A Project Admin who agreed could not do it: `artifact:approve_code_review` is
held by `architect` alone. The advice and the enforcement named different people and
nothing anywhere reported a problem, because a plausible wrong answer is
indistinguishable from a right one.

`plan` had the identical fault, masked: it resolved to project_admin, who happens to
also hold `artifact:approve_plan`, so it looked fine while being wrong.

THE ROOT CAUSE IS NOT ANY SINGLE WRONG VALUE. It is three maps that describe the same
fact and could drift apart in silence:

    progression.STAGE_ORDER        the stages a run can sit at   (from AGENT_REGISTRY)
    routing.AGENT_OWNER_ROLE       who owns each
    permissions._PHASE_PERMISSION  which permission approves each
    frontend/lib/roles.ts          the fourth copy, checked here by parsing it

So these tests pin the maps against each other rather than against expected values.
A test that asserted `agent_owner_role("code_review") == "architect"` would pass while
the permission moved to another role.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.authz.catalog import (  # noqa: E402
    AgentOwnershipError, assert_agent_ownership, verify_agent_ownership,
)
from shared.authz.consequential import _owner_label  # noqa: E402
from shared.authz.permissions import (  # noqa: E402
    _PHASE_PERMISSION, _ROLE_PERMISSIONS,
)
from shared.governance import routing  # noqa: E402
from shared.governance.routing import (  # noqa: E402
    AGENT_OWNER_ROLE, UnknownAgentPhase, agent_owner_role, agent_owner_role_or_none,
)
from shared.services.orchestrator.progression import STAGE_ORDER  # noqa: E402

pytestmark = pytest.mark.unit


# -- the invariant ------------------------------------------------------------


def test_every_stage_owner_can_approve_that_stage():
    """THE ONE THAT WOULD HAVE CAUGHT IT. Runs at boot too."""
    assert verify_agent_ownership() == []


def test_the_boot_guard_raises_rather_than_logging():
    """Code-against-code, so a failure is a developer's bug, never a deployment state.
    Logging it would let the product ship naming an approver who cannot approve."""
    assert_agent_ownership()  # does not raise today

    original = dict(AGENT_OWNER_ROLE)
    try:
        AGENT_OWNER_ROLE["code_review"] = "project_admin"  # the historical bug
        with pytest.raises(AgentOwnershipError, match="cannot approve"):
            assert_agent_ownership()
    finally:
        AGENT_OWNER_ROLE.clear()
        AGENT_OWNER_ROLE.update(original)

    assert verify_agent_ownership() == [], "the fixture leaked"


def test_every_runnable_stage_has_an_owner():
    """A stage a run can reach with no owner is a gate with nobody behind it."""
    missing = [s for s in STAGE_ORDER if agent_owner_role_or_none(s) is None]
    assert not missing, f"stages in STAGE_ORDER with no owner: {missing}"


def test_every_runnable_stage_has_an_approve_permission():
    missing = [s for s in STAGE_ORDER if s not in _PHASE_PERMISSION]
    assert not missing, f"stages with no entry in _PHASE_PERMISSION: {missing}"


def test_no_approve_permission_is_held_by_nobody():
    """A permission granted to no role is unreachable — the gate cannot be passed.

    The data half of this (a role no USER holds) is `warn_unheld_owner_roles`, which
    warns rather than failing, because an unstaffed role is a normal tenant state.
    """
    orphans = [
        (stage, _PHASE_PERMISSION[stage])
        for stage in STAGE_ORDER
        if not any(
            _PHASE_PERMISSION[stage] in ps for ps in _ROLE_PERMISSIONS.values()
        )
    ]
    assert not orphans, f"approve permissions no role grants: {orphans}"


# -- the specific regressions -------------------------------------------------


def test_code_review_resolves_to_the_role_that_can_approve_it():
    """The live defect, named so a failure says which one came back."""
    holders = [r for r, ps in _ROLE_PERMISSIONS.items()
               if "artifact:approve_code_review" in ps]
    assert agent_owner_role("code_review") in holders
    assert agent_owner_role("code_review") != "project_admin"


def test_the_consequential_message_names_the_role_that_can_approve():
    """What the user is actually told. It said 'a Project Admin' for Code Review."""
    assert _owner_label("code_review") == "an Architect"
    assert _owner_label("plan") == "a Scrum Master"


def test_every_owner_role_has_a_human_readable_label():
    """`plan` fixed to scrum_master immediately exposed that _owner_label had no entry
    for it, degrading the message to the generic fallback."""
    unlabelled = [
        s for s in STAGE_ORDER if _owner_label(s) == "this agent's owner"
    ]
    assert not unlabelled, f"stages whose owner has no spoken label: {unlabelled}"


def test_the_ui_phase_name_still_resolves():
    """The frontend sends `review`; the backend stage is `code_review`. Both must work
    — the alias is why re-keying the map did not break agent-access routing."""
    assert agent_owner_role("review") == agent_owner_role("code_review")


# -- the default that hid it --------------------------------------------------


def test_an_unknown_phase_raises_instead_of_answering_project_admin():
    """THE ROOT CAUSE. `.get(phase, "project_admin")` turned every miss into a
    confident wrong answer."""
    with pytest.raises(UnknownAgentPhase):
        agent_owner_role("not_an_agent")


def test_an_absent_phase_is_distinguishable_from_an_unknown_one():
    """Governance requests may legitimately carry no phase. That is a different fact
    from a phase that names nothing, and the two must not share an answer."""
    assert agent_owner_role_or_none("") is None
    assert agent_owner_role_or_none(None) is None
    assert agent_owner_role_or_none("not_an_agent") is None


def test_a_phaseless_agent_access_request_does_not_invent_an_owner_stage():
    """Preserved behaviour: with no phase there is no owner to advance to, so the
    Project Admin's decision is final. Previously this fell out of the default by
    accident; now it is deliberate and logged."""
    assert routing.next_agent_access_stage("project_admin", "") is None


def test_a_real_phase_still_advances_to_its_owner():
    assert routing.next_agent_access_stage("project_admin", "design") == "agent_owner"
    assert routing.agent_access_approver("agent_owner", "design") == "architect"
    # Documentation's owner IS the Project Admin, so stage one was already theirs.
    assert routing.next_agent_access_stage("project_admin", "documentation") is None


# -- the staffing check must be able to see the data --------------------------


def test_the_staffing_check_sets_the_tenant_guc_and_does_not_derive_tenants_from_rls():
    """THE TRAP THIS FELL INTO ONCE ALREADY.

    `role_bindings` has FORCE RLS on `app.current_tenant_id`, and the app role is not
    a Postgres superuser. The first version of `warn_unheld_owner_roles` took its
    tenant list from `SELECT DISTINCT tenant_id FROM role_bindings` without setting
    the GUC — so it read zero rows, found zero tenants, and cheerfully logged "every
    stage owner role has a holder in every tenant" while 17 real bindings sat there
    unread. A check that cannot fail is worse than no check.

    Structural, because the failure mode is a *passing* result: an assertion on the
    return value would have been green for the broken version too.
    """
    import inspect

    from shared.authz.catalog import warn_unheld_owner_roles

    src = inspect.getsource(warn_unheld_owner_roles)
    assert "app.current_tenant_id" in src, (
        "the tenant GUC is never set, so every role_bindings read returns nothing"
    )
    assert "FROM organizations" in src, (
        "the tenant list must come from organizations (no RLS), not from an "
        "RLS-protected table that reads empty before the GUC is set"
    )
    assert "DISTINCT tenant_id FROM role_bindings" not in src


# -- the fourth copy ----------------------------------------------------------


def test_the_backend_owner_map_agrees_with_the_frontend():
    """`frontend/lib/roles.ts::AGENT_OWNER_ROLE` is the fourth place this fact lives.
    Parsed rather than duplicated, so this fails when they drift instead of when
    somebody remembers to update a list here."""
    roles_ts = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "roles.ts"
    if not roles_ts.exists():          # backend-only checkout
        pytest.skip(f"{roles_ts} not present")

    block = re.search(
        r"export const AGENT_OWNER_ROLE:[^{]*\{(.*?)\n\};",
        roles_ts.read_text(encoding="utf-8"), re.S,
    )
    assert block, "could not find AGENT_OWNER_ROLE in roles.ts"

    frontend = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\s*(\w+):\s*\"(\w+)\"", block.group(1), re.M)
    }
    assert frontend, "parsed no entries — the regex has gone stale"

    mismatched = {
        phase: (role, backend_role)
        for phase, role in frontend.items()
        if (backend_role := agent_owner_role_or_none(phase)) is not None
        and backend_role != role
    }
    assert not mismatched, (
        "frontend and backend disagree on who owns these agents "
        f"(phase: frontend, backend): {mismatched}"
    )

    unknown = sorted(p for p in frontend if agent_owner_role_or_none(p) is None)
    assert not unknown, f"the frontend knows agents the backend does not: {unknown}"
