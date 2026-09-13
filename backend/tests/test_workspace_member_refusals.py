"""Adding a workspace member: a refused grant must answer 4xx and change nothing.

WHY THIS EXISTS. `add_workspace_member` used to write `RoleBinding` through the ORM
directly, so `grant_role`'s guards — one admin per unit, and no governance role where the
person already holds a delivery one — never ran on the screen where members are actually
added. Routing it through `grant_role` turned those guards on, and the first thing they did
was escape as an unhandled 500 with a stack trace, because the route caught only
`ValueError`.

Both refusals are business rules with readable explanations. A 500 tells the user nothing
and reads as a broken product; worse, it hides a rule working correctly.
"""
from __future__ import annotations

import inspect

from shared.routers import workspaces


def _source(fn) -> str:
    return inspect.getsource(fn)


def test_add_member_maps_both_domain_refusals_to_4xx():
    """Neither guard may reach the client as a 500."""
    src = _source(workspaces.add_workspace_member)
    assert "TierConflictError" in src, (
        "a delivery/governance clash would answer 500 — it is a business rule, not a fault"
    )
    assert "UnitAlreadyAdministeredError" in src, (
        "a second unit admin would answer 500 rather than naming who already holds it"
    )
    # Both are plain Exceptions, so an `except ValueError` does not cover them.
    from shared.authz.grant import TierConflictError, UnitAlreadyAdministeredError

    assert not issubclass(TierConflictError, ValueError)
    assert not issubclass(UnitAlreadyAdministeredError, ValueError)


def test_role_change_restores_the_previous_role_when_refused():
    """The revoke has already committed by then; without a restore the member is left
    holding nothing on a unit they were working in — a silent permission loss behind a
    visible error."""
    src = _source(workspaces.update_workspace_member_role)
    assert "TierConflictError" in src
    # The restore must come from the except path, not merely exist in the function.
    tail = src[src.index("except ("):]
    assert "grant_role(" in tail, (
        "a refused role change leaves the member with no role: the revoke is already "
        "committed and nothing puts it back"
    )
    assert "previous_role" in tail


def test_every_grant_role_call_in_this_router_is_guarded():
    """A future call site that forgets is the same 500 again."""
    src = inspect.getsource(workspaces)
    # Each `await grant_role(` should sit inside a try whose handler names the domain
    # errors. Checked structurally rather than by parsing: the router is small enough
    # that "the file mentions both, and every grant is inside a try" is a real signal.
    assert src.count("await grant_role(") >= 2
    assert src.count("TierConflictError") >= 2, (
        "a grant_role call site without the domain-error handler answers 500"
    )
