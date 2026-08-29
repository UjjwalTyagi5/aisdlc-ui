# Approval Workflow B — Gate Integrity Hardening — Design

## 1. The problem

`decide()` (`backend/shared/services/governance_requests.py`) is the single function every
`POST /governance-approvals/{id}/decide` call runs through, for all 17 governance request
types. Its entire "is this yours to answer?" check is one line:

```python
if decider_role != request["currentApproverRole"]:
    raise NotYourQueue(...)
```

`decider_role` comes from `effective_platform_role()` (`shared/authz/effective_role.py`),
which resolves a caller's **single, scope-agnostic "platform role"**: the highest-standing
role name they hold *anywhere in the tenant* (`_STANDING = ("org_admin", "bu_admin",
"project_admin")`, highest wins), with no reference to which specific project or business
unit the request names.

The result: for every request type whose approver is meant to be *the specific entity's*
admin — this project's Project Admin, this business unit's BU Admin — the check only
verifies the decider holds *a* binding of the right role *somewhere*, not *the* binding that
actually covers this request. Two concrete instances of this were already found and
live-verified during sub-project A:

- **`cross_bu_assignment`**: routed sideways to the specific unit's own BU Admin
  (`routing.py`'s own comment says so), but any `bu_admin` in the tenant can decide it. Live-
  verified: Payments' BU Admin (not the contributor's home unit) successfully approved a
  request raised against, and notified to, Lending's BU Admin.
- **`agent_access` stage two**: routed to the agent's owning delivery role
  (`AGENT_OWNER_ROLE[phase]`) on the request's own project, but any holder of that role
  anywhere in the tenant can decide it, once sub-project A's Task 8 gave those roles
  `governance:decide` in the first place.

Investigating for this spec confirmed the gap is **structural, not limited to these two
types** — every tier-routed and fixed-approver-role type shares the same unscoped check.
A `project_admin` on Project X can currently decide a `connector_access`, `budget_increase`,
`project_archive`, `project_settings_change`, `agent_default_project`, `user_onboarding`, or
`access_request` request raised on a completely unrelated Project Y. A `bu_admin` similarly
crosses business units for `project_creation`, `role_assignment`, `agent_default_workspace`,
and any tier-escalated request. Nothing else in the request path narrows this — the
`governance:decide` permission dependency is a bare floor, not scoped either.

`org_admin` is the one role that is genuinely tenant-wide by design (one administrative
tier over the whole organization), so `org_admin`-approved decisions are correctly
unscoped today and need no change.

## 2. Why this matters

This is a real authorization bypass on the request-decision path, not a theoretical one —
both known instances were live-verified against real data, and the mechanism generalizes
to every other type sharing the same check. It fits squarely under the "gate integrity
hardening" sub-project named during the original brainstorming session, and is more
extensive than what was known when that name was chosen.

## 3. Every request always has a workspace; a project is optional

`create_request()`'s signature (`governance_requests.py`) requires `workspace_id: str`
(no default) and accepts `project_id: Optional[str] = None`. So every request carries at
least a business-unit scope; some also carry a specific project scope. This is the anchor
the fix hangs off: no new field is needed on the request row.

## 4. The fix

Add one function, `decider_covers_scope(db, *, decider_id, role, request) -> bool`, called
alongside (not replacing) `decide()`'s existing role-name check. The name check still
answers "does this role, in the abstract, get to decide requests of this type at this
stage" (unchanged); the new check answers "does *this specific person* hold *that role* at
a binding that actually covers *this* request."

```python
async def decider_covers_scope(
    db: AsyncSession, *, decider_id: str, role: str, request: dict[str, Any]
) -> bool:
    """Does `decider_id`'s own `role` binding actually cover this request's scope?

    org_admin is tenant-wide by design — always covers everything, no query needed.
    Every other approver role must be scoped to the specific entity the request names:
    bu_admin to the request's workspaceId, project_admin (and any AGENT_OWNER_ROLE
    delivery role deciding agent_access stage two) to the request's projectId.

    THIS QUERIES role_bindings DIRECTLY, not effective_platform_role()'s already-collapsed
    "highest standing wins" single string — that collapsing is correct for "which role does
    this person route requests AS" but throws away exactly the specific-binding information
    this check needs. A decider can hold the right role at the right scope even when it
    is not their tenant-wide highest-standing role in the sense _STANDING orders roles;
    what matters here is only whether *a* live binding of `role` at the right scope exists,
    independent of what effective_platform_role() collapsed them to for the earlier
    name-match.
    """
    if role == "org_admin":
        return True
    if role == "bu_admin":
        scope_kind, scope_id = "business_unit", request.get("workspaceId")
    else:
        # project_admin, or any AGENT_OWNER_ROLE delivery role deciding agent_access
        # stage two — every non-bu_admin, non-org_admin approver role this system has
        # is project-scoped.
        scope_kind, scope_id = "project", request.get("projectId")
    if not scope_id:
        return False
    row = (
        await db.execute(
            text(
                f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                "  AND rb.role_name = :role AND rb.scope_kind = :sk AND rb.scope_id = CAST(:sid AS uuid)"
            ),
            {
                "u": decider_id,
                "role": role,
                "sk": scope_kind,
                "sid": scope_id,
                "now": datetime.now(tz=timezone.utc),
            },
        )
    ).first()
    return row is not None
```

(`live_binding()` — `shared/authz/read_scope.py` — already the standing helper for "not
expired, not revoked"; reused here rather than reinvented.)

Called in `decide()` right after the existing role-name check:

```python
if decider_role != request["currentApproverRole"]:
    raise NotYourQueue(...)
if not await decider_covers_scope(db, decider_id=decider_id, role=decider_role, request=request):
    raise NotYourQueue(...)  # same error — the caller learns nothing about WHY, deliberately
```

Same exception, same message shape as the existing check: from the caller's point of view
this is just a stricter form of "not your queue," not a new error class the frontend needs
to learn.

## 5. `cross_bu_assignment` needs no special case

`routing.py`'s own comment on `cross_bu_assignment` confirms its `request["workspaceId"]`
is already set to the *lending* unit (the one whose contributor is being borrowed) — the
exact business unit whose admin should decide it. The generic `bu_admin` branch above
reads that same field, so this fix closes Finding 5 as a side effect of the general rule,
with no `if request_type == "cross_bu_assignment"` branch anywhere in the new code. This is
a good sign the design is the right shape, not a coincidence to paper over: it means the
field was already correct, only the check reading it was missing.

## 6. `agent_access` stage two needs no special case either

Stage two's approver role is a delivery role (`ba`, `architect`, `qa`, `devops_engineer`,
`security_engineer`, `data_engineer`), never `bu_admin`/`org_admin`, so it falls into the
`else` branch (`project` scope, matched against `request["projectId"]`) automatically. No
separate handling for "is this a delivery role" is needed — the branch is keyed on
`role != "bu_admin"` (with `org_admin` already handled above it), not on an explicit list
of delivery-role names, so a future `AGENT_OWNER_ROLE` addition needs no matching update
here.

## 7. Scope for this sub-project

**In scope:**
- The one new function and its one call site in `decide()`.
- A live-verified regression test proving the ORIGINAL bug for both already-found instances
  (wrong-unit `bu_admin` on `cross_bu_assignment`; wrong-project `architect` on
  `agent_access` stage two) is closed.
- A live-verified regression test for at least one tier-routed type not previously tested
  this way (e.g. `connector_access` or `budget_increase`), proving the fix is genuinely
  general and not narrowly patching only the two known cases.
- A test confirming the CORRECT decider (the actual project's Project Admin, the actual
  unit's BU Admin) is unaffected — this must not become a new false-negative that blocks
  legitimate decisions.
- A test confirming `org_admin` remains unscoped (an org_admin with no specific project/
  workspace binding at all can still decide anything routed to `org_admin`).

**Out of scope, explicitly:**
- The `_STANDING`/"highest standing wins" collapsing behavior in `effective_platform_role()`
  — noted during this spec's research as an adjacent, pre-existing wrinkle (a person who is
  `bu_admin` on one unit and `project_admin` on an unrelated project routes everywhere as
  `bu_admin`, which can make them fail the role-NAME check on their own project's requests).
  This is a different failure direction (over-restrictive, not a bypass) from what this
  sub-project fixes, was not reported as a live problem, and changing role-collapsing
  priority is a much larger behavioral change than adding a scope check. Left as a
  documented, not-a-finding observation for whoever next touches `effective_role.py`.
- Sub-project C (new request entry points) and D (break-glass) — unrelated, separate
  sub-projects.
- Any change to `escalate()`/`cancel()` — per `governance_requests.py`'s own router
  comment, `/cancel` is deliberately the initiator's own act and `/escalate` is
  deliberately open to the initiator too; this spec's fix is scoped to `/decide` only,
  matching where the actual finding was made.

## 8. Risk and rollback

The new check can only make `decide()` MORE restrictive than today — it adds a second
`AND`-ed condition on top of the existing role-name check, never grants access the current
code refuses. The only failure mode to guard against in testing is a false negative: a
genuinely correct decider (the request's own project's Project Admin, its own workspace's
BU Admin, org_admin) being wrongly refused. Section 7's third bullet test exists
specifically to catch this before it ships.
