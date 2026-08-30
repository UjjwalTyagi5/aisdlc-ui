# Request-type baseline audit — live-traced, 2026-08-29

Task 1 of the "approval workflow A: effect reachability" plan. Every one of the 17
entries in `shared/governance/routing.py::REQUEST_TYPES` was raised and decided live
against the running backend (`uv run uvicorn process_api:app --host 127.0.0.1 --port
8001`) and the real `pwc` tenant (`8d5bd6a3-7e07-46ce-8416-cada90dead79`) in the local
Postgres (port 5433), using the actual HTTP filing points a real user/client would hit
(the generic `POST /governance-approvals` for hand-raised types, the dedicated
typed endpoints for the rest), then the DB was queried directly to confirm what
actually got written. **This file is not deleted at the end of the plan** — later
tasks and the final whole-branch review read it to confirm every finding below was
addressed or explicitly parked.

This document is NOT deleted at the end of the plan. It is the evidence trail.

## Setup used for the trace

Extra fixtures added to the existing `pwc` tenant (not deleted — harmless, mirrors
real org structure): a `bu_admin` on the Payments unit, a `project_admin` + `developer`
+ `architect` bound to a real active project ("Trace Project 1"), two throwaway
projects (one becomes "Trace Project 2" via `project_creation`, one is archived), and
an inactive `model_providers` row. The existing Lending unit's real `bu_admin` and
`contributor` (from earlier session work) were reused for `cross_bu_assignment` and
`role_assignment`. Tokens were minted with `config.auth.jwt.create_access_token`,
carrying each role's actual shipped `role_permissions` set (queried from the DB, not
guessed) — role for ROUTING purposes still comes from real `role_bindings` rows,
exactly as `effective_platform_role` derives it in production.

## Results table

| type | raised as | decided as | observed result | matches effects.py's claimed behavior? |
|---|---|---|---|---|
| `project_creation` | project_admin, via `POST /projects` (pending) | bu_admin | 201 → 200; project flipped `pending_approval` → `active` | Y |
| `model_credential` | developer (contributor-tier), generic endpoint | project_admin | 201 → 200; no DB effect (decision is the outcome) | Y — by design |
| `budget_increase` | bu_admin, dedicated `POST /workspaces/{id}/budget-increase-request` | org_admin | 201 → 200; `workspaces.monthly_budget_usd` set to the requested amount | Y, for workspace scope. **Project scope is dead code** — see Finding 1 |
| `project_archive` | bu_admin, via `POST /projects/{id}/archive` (files a request) | org_admin | 200 → 200; `projects.archived` set true | Y |
| `project_settings_change` | project_admin, via `PATCH /projects/{id}` (queued) | bu_admin | **Was a 500** (`UndefinedColumnError: description`) before this task's fix; now 200 → 200, `display_name` updated, `description` silently ignored | **N before fix, fixed inline — see Finding 2** |
| `agent_default_org` | system-raised, `AgentProfile.propose()` | org_admin (tier owner) | Not individually re-traced (see below) | Y, by shared dispatch + existing test |
| `agent_default_workspace` | system-raised, `AgentProfile.propose()` | bu_admin (tier owner) | Not individually re-traced (see below) | Y, by shared dispatch + existing test |
| `agent_default_project` | system-raised, `AgentProfile.propose()` | project_admin (tier owner) | Full live cycle exercised by `backend/tests/agent_profiles/test_evaluation_gate.py::test_evaluate_propose_approve_publishes_it` (draft → evaluate(pass) → propose → `POST /governance-approvals/{id}/decide` → published version `is_active=True`), re-run and confirmed passing during this task | Y |
| `connector_access` | developer, generic endpoint | project_admin | 201 → **422 `EFFECT_UNAVAILABLE`**: "This request names no connector to grant." | **N — matches a known, in-scope gap.** See Finding 3 |
| `mcp_server` | developer (contributor-tier) **and** bu_admin (post-fix), generic endpoint | project_admin / org_admin | Both: 201 → 200; no DB effect (decision is the outcome, per current `_DECISION_IS_THE_OUTCOME`) | Y — the bu_admin-tier raise is this task's fix; see "Task 1's own fix" below |
| `agent_access` | developer, generic endpoint, `phase=development` | project_admin (stage 1) → architect (stage 2, AGENT_OWNER_ROLE["development"]) | Stage 1: 201 → 200, advances to `pending_review`/stage `agent_owner`. Stage 2: **403 Forbidden** — architect's token, minted with the ACTUAL shipped `architect` permission set, lacks `governance:decide` | **N — a real, systemic gap.** See Finding 4 |
| `access_request` | developer, generic endpoint | project_admin | 201 → 200; no DB effect (decision is the outcome) | Y — by design |
| `user_onboarding` | developer, generic endpoint (hand-raised variant) | project_admin | 201 → 200; no DB effect (decision is the outcome, matches current code; a later task in this plan is expected to add one) | Y — matches current code |
| `role_assignment` | system-raised, `POST /onboarding` (role=contributor, into Lending) | lending bu_admin | Direct `decide()`: **422 `EFFECT_UNAVAILABLE`** ("closes when the role is actually assigned... not by approving it") — exactly as documented. Closed for real via `PATCH /workspaces/{id}/members/{userId}` → request auto-flipped to `approved` | Y — exactly as designed |
| `cross_bu_assignment` | project_admin, `POST /projects/{id}/access-requests` (borrowing Lending's contributor onto a Payments project) | **the WRONG unit's bu_admin (Payments) succeeded**; the correct unit's bu_admin (Lending) then got `409 ALREADY_CLOSED` | 200 (wrong approver) — `cross_bu_grants` row + `role_bindings` (project-scope `developer`) both written correctly, but by someone with no standing to decide this specific request | **N — a real authorization gap.** See Finding 5 |
| `model_provider_access` | bu_admin, generic endpoint | org_admin | 201 → **422 `EFFECT_UNAVAILABLE`**: "That provider no longer exists." `targetRef` on the raised request was the WORKSPACE id (there is no payload field on the generic endpoint to carry a provider id) | **N — matches the same known, in-scope gap as connector_access.** See Finding 3 |
| `other` | developer, generic endpoint | project_admin | 201 → 200; no DB effect (decision is the outcome) | Y — by design, permanent |

## Task 1's own fix, confirmed live

`_BU_ADMIN_RAISABLE` (`backend/shared/governance/routing.py`) was missing `mcp_server`
even though the frontend's mirror (`frontend/lib/requests/routing.ts::BU_ADMIN_RAISABLE`)
already had it — a Business Unit Admin saw "Request an MCP server" in the picker and
got a 403 `TYPE_NOT_RAISABLE` on submit. Reproduced live pre-fix (403), fixed by adding
`"mcp_server"` to the tuple, pinned by `backend/tests/test_governance_routing.py`
(2 new tests, both passing), and re-verified live post-fix: a bu_admin token raising an
`mcp_server` request now gets `201`, and org_admin can decide it (`200`).

Note: this fix was not visible over HTTP until the backend process was restarted —
`uvicorn --reload`'s file-watcher did not pick up the `routing.py` edit (it reloaded
once, for the test file's creation, and never again despite further edits to
`routing.py`, `effects.py` and `projects.py` in this task). Anyone re-running these
traces against a long-lived `--reload` process should restart it rather than trust the
watcher, or they will see stale pre-fix behavior. Not filed as a product bug — it's a
property of `--reload` + this environment, not of the application.

## Findings beyond the one assigned bug

### Finding 1 — `budget_increase`'s project-scope branch is dead code (PARKED)

`_apply_budget_increase` (`shared/governance/effects.py`) branches on
`request.get("projectId")`: if present, it updates the PROJECT's
`monthly_budget_usd`; otherwise the WORKSPACE's. But the only HTTP filing point for
`budget_increase` is `POST /workspaces/{workspace_id}/budget-increase-request`
(`shared/routers/workspaces.py`), which always sets `workspace_id`/`target_ref` and
never a `project_id` — confirmed by grep, no `projects.py` route exists for this. A
Project Admin whose PROJECT (not their whole unit) has exhausted its budget has no way
to raise a project-scoped `budget_increase` at all; the project branch in
`effects.py` has been unreachable since it was written.

**Ruling: parked, not fixed inline.** Not a one-line fix — it needs a new dedicated
project-level filing endpoint (mirroring the workspace one) plus frontend wiring, which
is out of this plan's stated scope (Tasks 2-9 per `progress.md`'s pre-flight table only
touch connector/mcp/model-provider/onboarding prefill and the `_DECISION_IS_THE_OUTCOME`
dispatch for model_credential/mcp_server/agent_access/user_onboarding). Belongs to
whichever later sub-project owns budget/project-admin ergonomics.

### Finding 2 — `project_settings_change` crashed on `description` (FIXED INLINE)

The `projects` table has no `description` column (confirmed via
`information_schema.columns`; the `Project` ORM class maps no such attribute either).
Live-tracing this type surfaced two consequences of the same drift:

- **Queued path** (a Project Admin editing a project they don't own directly →
  `_queue_settings_change` → later approved): `_apply_project_settings_change` built
  `UPDATE projects SET description = $1, ...` and the DB rejected it —
  `asyncpg.exceptions.UndefinedColumnError`, surfaced to the caller as a raw
  `500 Internal Server Error` (no JSON body). Worse than a refused approval: the
  transaction rolled back the status flip too, so the request was stuck open forever —
  every future approval attempt hit the same 500.
- **Direct path** (bu_admin/org_admin editing their own project via `PATCH
  /projects/{id}`): `project.description = body.description` silently sets an
  unmapped Python attribute on the ORM object — no error, but nothing is persisted and
  `ProjectOut` doesn't even serialize a `description` field, so the response gives no
  indication either way. Quieter, but the same root cause.

**Ruling: fixed inline**, same bar as the `mcp_server` drift this task was already
assigned — removing a stale entry from a lookup table two files agree should be kept in
sync (`_SETTINGS_FIELDS` in `effects.py`, mirrored by `_SETTINGS_PAYLOAD_KEYS`/
`_SETTINGS_FIELD_LABEL` in `projects.py`). `description` is now absent from all three,
which (per `_apply_project_settings_change`'s own docstring: "a field the project no
longer has... is ignored rather than fatal") makes a description edit a silent no-op on
approval instead of a crash — consistent with the direct-write path's existing (if also
quiet) no-op. Pinned by a new test,
`test_approving_a_settings_change_with_a_dead_description_field_does_not_crash` in
`backend/tests/test_governance_requests.py`, which raises a settings-change request with
both a real field (`name`) and the dead one (`description`) in the same payload and
asserts approval succeeds and the real field applies. The direct-write path's silent
no-op (PATCH accepts a `description` and reports success while persisting nothing) is
NOT touched by this fix — it doesn't crash or block anything, so it is noted here but
left for whoever eventually decides whether `description` should be a real column or
removed from the API surface entirely.

### Finding 3 — `connector_access` and `model_provider_access` can't carry what their effect needs (NOTED, not parked — already in this plan's scope)

Both types now have a real `apply_on_approve` effect that reads structured data off
the request: `connector_access` needs `payload.targetId`/`kind`/`access`;
`model_provider_access` needs `target_ref` to be the `model_providers.id` being
activated. But the only way to hand-raise either is the generic `POST
/governance-approvals` endpoint, whose `RequestCreateIn` (backend) /
`RequestCreateInput` (frontend) accepts no `payload` and sets `target_ref` to
`projectId or workspaceId` — never anything about a connector or a provider. Live
traced: raising either succeeds (`201`), but approving either always fails —
`connector_access` with `EFFECT_UNAVAILABLE: "This request names no connector to
grant."`, `model_provider_access` with `EFFECT_UNAVAILABLE: "That provider no longer
exists."` (since `target_ref` was a workspace id, not a provider id). Today, every
`connector_access`/`model_provider_access` request raised through the standard picker
is un-approvable.

**Not parked — this is exactly what this plan's Tasks 2-5 exist to fix.**
`progress.md`'s pre-flight table already describes `RaiseRequestPrefill` adding
`targetId`/`accessLevel` (connectors and MCP) and `providerModel` (model provider
access) fields threaded through `service.create_request`'s payload merge. This
live trace independently confirms the exact failure mode those tasks are aimed at,
from the raising side rather than a static read of `effects.py`. No action taken in
Task 1 beyond recording it here for the later tasks to reference.

### Finding 4 — `agent_access` stage two is unreachable for 12 of 13 phases (PARKED)

Stage two's approver is `agent_owner_role(phase)`
(`shared/governance/routing.py::AGENT_OWNER_ROLE`), which names a delivery role for
every phase except `documentation` (`project_admin`): `ba` (requirements), `architect`
(design/development/review/discovery/strategy/migration_mapping), `security_engineer`
(security), `qa` (testing/validation), `devops_engineer` (deployment), `data_engineer`
(data_engineering). But `POST /governance-approvals/{id}/decide` is gated on the
`governance:decide` PERMISSION (`shared/routers/governance_requests.py`), and queried
directly from `role_permissions` (no tenant override exists), only `bu_admin` and
`project_admin` hold it — confirmed against the real `role_permissions` table, not
assumed. Live-traced: a developer raised an `agent_access` request with
`phase=development`; the project_admin's stage-one approval correctly advanced it to
stage two (`agent_owner`, routed to `architect` per `AGENT_OWNER_ROLE`); an architect
token minted with the architect role's actual shipped permissions then got a flat
`403 Forbidden` from the permission dependency — never even reaching `decide()`'s own
role check (`decider_role != request["currentApproverRole"]`), because the permission
floor blocks it first.

Net effect: stage two of `agent_access` can only ever be completed for the
`documentation` phase. For every other phase, the request is permanently stuck at
stage two — nobody who is actually the agent's owner can decide it, and nobody else is
allowed to either (the self-approval/role-match rule in `decide()` would refuse a
project_admin trying to decide their own stage-one-approved request a second time
anyway).

**Ruling: parked, not fixed inline.** Not a one-line fix — widening `governance:decide`
to `ba`/`architect`/`qa`/`devops_engineer`/`security_engineer`/`data_engineer` in
`role_permissions` would let those roles decide **every** `governance:decide`-gated
action platform-wide (budget increases, connector grants, etc.), not just their own
agent's stage-two — that permission string isn't scoped to agent ownership. A correct
fix likely needs either a narrower, purpose-built permission/check specifically for
"this decider is the request's own stage-two owner" (bypassing the blanket
`governance:decide` gate the way `decide()`'s role-match already does for the routing
side), or restructuring how stage two is authorized. This directly affects whether
Task 8 (which, per `progress.md`, gives `agent_access` a real `apply_on_approve`
effect) is reachable in practice for any phase but documentation — flagging it now so
Task 8's implementer checks this before assuming stage two works.

### Finding 5 — `cross_bu_assignment` can be decided by the wrong unit's bu_admin (PARKED, security-relevant)

The type's own routing comment (`shared/governance/routing.py`) says its approver is
"SIDEWAYS to a SPECIFIC Business Unit Admin — the one who owns the contributor being
borrowed... Bumping it sent a Business Unit Admin's ask to the Organization Admin, who
has no standing." But `decide()`'s "is it yours to answer?" check
(`shared/services/governance_requests.py`) is `decider_role != request["currentApproverRole"]`
— a plain ROLE-NAME comparison (`"bu_admin" == "bu_admin"`), with no comparison against
`request["workspaceId"]` (the specific unit whose admin should decide). Nothing else in
the request path (`require_permission("governance:decide")` is a bare permission
check, not workspace-scoped for this route) narrows it either.

Live-traced: Payments' bu_admin (not the contributor's home unit) successfully decided
(`200`, `approved`) a `cross_bu_assignment` request that was raised against, and
notified to, Lending's bu_admin — Lending's actual bu_admin then got `409
ALREADY_CLOSED` when trying to decide the same request. The unauthorized approval still
fully applied: both the `cross_bu_grants` row and the `role_bindings` seat were written.
Any bu_admin anywhere in the org can currently approve a loan of any other unit's
contributor onto any project they can see, regardless of which unit's admin the request
was actually routed to.

**Ruling: parked, not fixed inline.** Scoping `decide()` to the specific unit for this
one type (and verifying no other `TYPE_ROUTED` entry has the same shape, since this
`if request_type != "cross_bu_assignment"` bump-exception is the only sideways-not-up
routing case) is a real, non-trivial change to a shared, heavily-relied-on function
that every other type also goes through — not the same bar as the `mcp_server`
one-tuple-entry fix. Out of this plan's stated scope (effect reachability for existing
types' approve effects, not decide-time authorization). Recommended as a follow-up
task, ideally in whichever sub-project owns cross-tenant/cross-unit authorization
correctness.

## Secondary observation (not a ruling, no action needed)

`POST /projects/{id}/archive` is gated on `workspace:manage`
(`shared/routers/projects.py`), which only `bu_admin` and `org_admin` hold (confirmed
against `role_permissions`) — `project_admin` does not. The function's own docstring
("A Project Admin archives their own project... directly") describes a branch
(`role != "bu_admin"` falls through to a direct archive) that a Project Admin can
never actually reach, since the permission dependency 403s them before the function
body runs. Not filed as a finding requiring a ruling — it doesn't lose data or crash,
it's just a comment describing a keystroke nobody has. Noted here in case a later task
touches this endpoint's permission floor.

## Summary

- 17/17 request types live-traced.
- 1 confirmed-fixed drift (this task's assignment): `mcp_server` now raisable by a
  Business Unit Admin, backend matches frontend, pinned by
  `backend/tests/test_governance_routing.py`.
- 1 additional bug found and fixed inline (same bar): the `project_settings_change`
  `description`-column crash, pinned by a new test in
  `backend/tests/test_governance_requests.py`.
- 2 types (`connector_access`, `model_provider_access`) confirmed broken in exactly the
  way this plan's later tasks (2-5) are already scoped to fix — noted, not re-parked.
- 3 new findings beyond this plan's scope, parked with reasons: `budget_increase`'s
  dead project-scope branch (Finding 1), `agent_access` stage two being unreachable
  for 12 of 13 phases (Finding 4 — flagged for Task 8 specifically), and
  `cross_bu_assignment`'s decide-time authorization gap (Finding 5).
- Every other type (`project_creation`, `project_archive`, `model_credential`,
  `agent_default_*`, `access_request`, `user_onboarding`, `role_assignment`, `other`)
  confirmed working exactly as `effects.py`/`routing.py` claim.
