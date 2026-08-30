# Gate scope audit — 2026-08-30

Task 1 of the "approval workflow B: gate integrity hardening" plan. Before
`decider_covers_scope` (Task 2) goes live inside `decide()`, this audits every one
of the 17 `REQUEST_TYPES` (`backend/shared/governance/routing.py`) for two things:
which role(s) can ever be `currentApproverRole` for it, and whether the scope field
that role's `ROLE_SCOPE` (`backend/shared/authz/permissions.py:43`) entry needs —
`workspaceId` for `business_unit`, `projectId` for `project` — is reliably
populated when that role is actually the one deciding.

Method: direct code reading of `routing.py`, `permissions.py`, `effects.py`,
`governance_requests.py`'s `create_request()`, every backend router that calls
`create_request()`, and every frontend raise path (`RaiseRequestDialog`,
`RequestAccessButton`, and each type's dedicated raise UI) that decides what
`workspaceId`/`projectId` actually get sent — cross-referenced against
`backend/.superpowers-findings/2026-08-29-request-type-baseline-audit.md`
(sub-project A's live-traced baseline) wherever it already answered the question.

`create_request()`'s signature requires `workspace_id: str` (no default) and
accepts `project_id: Optional[str] = None` — confirmed by direct read
(`governance_requests.py:301-327`). So every request always carries a business
unit; `org_admin` is tenant-wide by design and needs no check either way. The only
question that matters is: **when `project_admin` is `currentApproverRole`, is
`projectId` reliably set?**

| Type | Approver role(s) | ROLE_SCOPE | Scope field always set? |
|---|---|---|---|
| `project_creation` | bu_admin (TYPE_ROUTED; may still escalate to org_admin, which needs no check) | business_unit | Yes — `POST /projects` sets both `workspace_id` and `project_id` server-side (`projects.py:539-540`) unconditionally |
| `model_credential` | project_admin (contributor raiser) -> bu_admin (project_admin raiser); bu_admin cannot raise it (absent from `_BU_ADMIN_RAISABLE`) | project / business_unit | **Not reliably set when project_admin decides — see Concern 2.** The correct raise path (`projects/[id]/models/page.tsx`) always sets `projectId`; a second path (`admin/models/page.tsx`'s `ModelAvailabilityCard`, `audience="project"`) omits it — but that path is only reachable by a project_admin themselves (self-raise escalates to bu_admin, so it never actually lands a project_admin-tier decision project-less; it only means that specific request can never be approved once it reaches bu_admin, a pre-existing effects.py-completeness bug in the same family as the baseline audit's Finding 1, not a decider-scope gap). The **contributor-raised** case (generic dialog, `_CONTRIBUTOR_RAISABLE` includes `model_credential`) routes to project_admin tier and the raise dialog defaults `projectId` to "none" — see Concern 1 |
| `budget_increase` | bu_admin (project_admin raiser) -> org_admin (bu_admin raiser); never reaches project_admin (not in `_CONTRIBUTOR_RAISABLE`, so `can_raise_type` blocks any role below project_admin) | business_unit / organization | Yes — `POST /workspaces/{id}/budget-increase-request` always sets `workspace_id`, never `project_id` (baseline audit Finding 1), but since it never routes to project_admin as decider this is irrelevant to the scope check |
| `project_archive` | org_admin (TYPE_ROUTED) | organization | N/A — org_admin needs no check; `project_id`/`workspace_id` both set anyway (`projects.py:784-785`) |
| `project_settings_change` | bu_admin (tier-routed, `initiator_role` hardcoded to `"project_admin"`, so always escalates to bu_admin; may climb to org_admin) | business_unit | Yes — `workspace_id`/`project_id` both set server-side (`projects.py:888-889`), system-raised, no client input |
| `agent_default_org` | org_admin (TYPE_ROUTED) | organization | N/A — no check needed |
| `agent_default_workspace` | bu_admin (TYPE_ROUTED) | business_unit | Yes — `workspace_id` is `target.scope_id` directly for a workspace-scoped profile (`agent_profiles.py:876-877`) |
| `agent_default_project` | project_admin (TYPE_ROUTED) | project | Yes — `project_id` = `target.scope_id` for a project-scoped profile (`agent_profiles.py`/`agent_skills.py`); `workspace_id` is server-resolved through the project row (`_project_workspace_id`, `agent_profiles.py:874-875`), never client-supplied |
| `connector_access` | project_admin (contributor raiser) -> bu_admin (project_admin raiser) -> org_admin (bu_admin raiser) | project / business_unit / organization | **Not reliably set when project_admin decides — see Concern 1.** Effect-backstopped though: `_apply_connector_access` resolves `scope = "project" if projectId else "unit"` and the `"unit"` branch requires `currentApproverRole == "org_admin"`, so a project-less request can never actually be *approved* at project_admin tier — only wrongly authorized-to-decide (and thus reject) by an unrelated project_admin in the same business unit |
| `mcp_server` | project_admin (contributor raiser) -> bu_admin (project_admin raiser) -> org_admin (bu_admin raiser) | project / business_unit / organization | **A second type genuinely project-less by design, same shape as `user_onboarding`** — `_apply_mcp_server` never reads `projectId` anywhere (unit-level grant only, gated on `currentApproverRole == "org_admin"` regardless of tier routed); a project_admin-tier decision on it is always a pass-through/escalate step, never itself the grant. No concern |
| `agent_access` | project_admin (stage 1) -> `AGENT_OWNER_ROLE[phase]` (stage 2, a delivery role, never bu_admin/org_admin) | project (both stages) | Yes — raised only through its own dedicated dialog (`request-agent-access-dialog.tsx`), which always passes a real `projectId` prop (never client-optional the way the generic dialog's is); server never derives it any other way |
| `access_request` | project_admin (contributor raiser) -> bu_admin (project_admin raiser) -> org_admin (bu_admin raiser) | project / business_unit / organization | **Not reliably set when project_admin decides — see Concern 1. No effect-layer backstop** (`_DECISION_IS_THE_OUTCOME`: approving *is* the entire consequence, nothing downstream re-validates project scope) |
| `user_onboarding` | project_admin (contributor/developer raiser) -> bu_admin -> org_admin | project / business_unit / organization | **Confirmed absent by design** when a contributor/developer raises it: `_apply_user_onboarding` (`effects.py:1119-1183`) reads only `payload`/`workspaceId`, never `projectId` — the one case the spec's fallback names explicitly |
| `role_assignment` | bu_admin (TYPE_ROUTED, sideways-down) | business_unit | Yes — system-raised from `POST /onboarding`, `workspace_id` always set (`onboarding.py:281`), no `project_id` concept for this type at all |
| `cross_bu_assignment` | bu_admin (TYPE_ROUTED, sideways to the lending unit) | business_unit | Yes — `workspace_id` is the *lending* unit (`home.id`), `project_id` also always set (`project_members.py:378-379`); the generic bu_admin branch closes Finding 5 with no special case, confirmed by direct read |
| `model_provider_access` | org_admin (TYPE_ROUTED) | organization | N/A — no check needed; `workspace_id` set anyway |
| `other` | project_admin (contributor raiser) -> bu_admin -> org_admin | project / business_unit / organization | **Not reliably set when project_admin decides, but arguably by design** — `other` has no fixed semantic tying it to a project (it is literally the catch-all), and like `access_request` is `_DECISION_IS_THE_OUTCOME` with no effect-layer backstop. Grouped with Concern 1 for completeness, but weighted lower since "no project in view" is plausible for a genuinely miscellaneous ask |

## Concern 1 — the project-less fallback is reachable for more types than "by design," because nothing requires `projectId` when it should

The spec's own reasoning (design doc §4) frames the fallback as existing for
exactly one case: `user_onboarding`, which is *genuinely* project-less by design.
This audit confirms that case, and finds a second type in the same genuinely-safe
shape (`mcp_server`). But it also finds that the fallback is mechanically reachable
for **`connector_access`, `access_request`, `model_credential`, and `other`** —
types whose project scope is real and meaningful when it applies — because nothing
in the system requires `projectId` to be set even when a contributor raises one of
these from a project context:

- `RequestCreateIn.projectId` (`shared/routers/governance_requests.py:68`) is
  client-optional with no per-type server-side requirement.
- `RaiseRequestDialog`'s Project selector (`frontend/components/requests/raise-request-dialog.tsx:136,388`)
  defaults to `"none"` ("Not project-specific") for every type, including ones
  raised from `approvals/page.tsx`'s free-form "Raise a request" button with no
  prefill at all.
- Even a prefilled, project-context-aware raise button can omit it:
  `agent-readiness-section.tsx`'s `RequestAccessButton` for `connector_access`
  (rendered on the catalogue page, reachable by a contributor scoped to their own
  project) passes `{type, title, description}` only — no `projectId` — even
  though it is rendered from inside a specific project's readiness view
  (`frontend/components/catalogue/agent-readiness-section.tsx:113-117,173-181`).
  Contrast this with `projects/[id]/integrations/page.tsx`'s own
  `connector_access`/`mcp_server` buttons, which correctly set `projectId: id`
  (`frontend/app/(app)/projects/[id]/integrations/page.tsx:160,180`).

So a contributor can raise `connector_access` or `access_request` (both in
`_CONTRIBUTOR_RAISABLE`) with `projectId` unset — either through the untargeted
dialog or, for `connector_access`, even through a project-scoped entry point that
simply never wires the field through. The request then tier-routes to
`project_admin` (bottom of `REQUEST_ESCALATION_CHAIN`), and lands exactly on
`decider_covers_scope`'s fallback branch: **any `project_admin` bound to *some*
project in the request's own business unit** — not the specific project the
requester meant — is authorized to decide it.

**Severity differs by type:**
- `connector_access` and `model_credential` are backstopped by `effects.py`:
  `_apply_connector_access`'s `"unit"`-scope branch refuses any tier but
  `org_admin`, and `_apply_model_credential` refuses outright with no
  `project_id`. A wrongly-scoped project_admin can pass the *decide()* gate but
  can never actually complete an *approval* — the worst they can do is wrongly
  **reject** a request that was never theirs to judge (a real, but narrower,
  integrity/audit problem, not a grant bypass).
- `access_request` and `other` have **no such backstop** — for these two,
  `_DECISION_IS_THE_OUTCOME` (`effects.py:113-122`): approving *is* the entire
  consequence, and nothing downstream re-validates that the approver actually
  administers the project the ask was about. A project_admin from an unrelated
  project in the same business unit can fully approve an `access_request`
  intended for a project they have no standing on, purely because the requester
  (accidentally or otherwise) left the Project field on "Not project-specific."
  This is the sharper of the two: it is a genuine narrowing of the exact-project
  guarantee the fix is meant to provide, reachable today by any contributor with
  nothing more than the default state of an existing form control.

This does not make the fix in the spec wrong or unsafe to ship — `decider_covers_scope`
is still strictly narrower than today's fully-unscoped check for every one of these
cases (it now requires *some* binding in the *correct business unit*, where today
it requires nothing more specific than the role name anywhere in the tenant), and
the mechanism itself is exactly as documented, generic rather than type-specific,
matching the design's own stated intent not to special-case per type. But the
audit's premise — that only one type (`user_onboarding`) is expected to exercise
the coarser fallback — is not accurate in practice, and `access_request`
specifically deserves attention before or alongside Task 3, since it is the one
row where the fallback's coarseness is the *entire* remaining check with nothing
else to catch a wrong approval.

## Concern 2 — `model_credential`'s admin/models raise path is missing `projectId` (effect-completeness, not gate-integrity)

`admin/models/page.tsx` renders `ModelAvailabilityCard` with
`audience={scope === "project" ? "project" : "bu"}`; when `audience === "project"`
the card computes `requestType = "model_credential"` but its `RequestAccessButton`
prefill only carries `workspaceId`, never `projectId`
(`frontend/components/app/model-availability-card.tsx:73,144-150`). Traced through
routing, this does **not** produce a project_admin-tier decision with a missing
project (the only role that can reach this view with `audience="project"` is a
project_admin, and a project_admin raising `model_credential` self-escalates one
tier to `bu_admin`, per `initial_approver_role`'s self-approval bump — so the
decider is always bu_admin, business-unit scope, `workspaceId` always present).
It does mean that specific request can never be *approved* once it reaches
bu_admin (`_apply_model_credential` refuses with no `project_id`) — the same
shape as the baseline audit's Finding 1 (a dead/broken raise path), not a
decider-scope gap. Noted for completeness; not this task's concern to fix.

## Conclusion

The design in the spec (`docs/superpowers/specs/2026-08-30-approval-workflow-b-gate-integrity-design.md`)
— exact-scope match for `bu_admin`/`project_admin`, with the project-less fallback
for `project_admin` only — is **mechanically correct and handles every row above
without a false negative**: no legitimate decider (the request's own project's
Project Admin, its own unit's BU Admin, any Org Admin) is ever wrongly refused by
it, and `business_unit`-scope rows are unconditionally safe because
`create_request()` requires `workspace_id` with no exceptions found anywhere in
the 17 types. `user_onboarding` is confirmed as the type the spec names by design;
`mcp_server` is a second, newly-confirmed type in the identical genuinely-safe
shape.

However, the audit surfaces a real completeness gap in the *assumption* underlying
the fallback's scope, not in the fallback's mechanics: **`access_request`** (and to
a lesser, effect-backstopped extent `connector_access`, `model_credential`, and
`other`) can reach `project_admin` tier with no `projectId` through ordinary,
already-shipped UI paths — not because the type is inherently project-less, but
because no raise path requires the field. For `access_request` specifically, since
approval has no downstream check at all, this means the fix's core guarantee (only
the request's own project's Project Admin can decide it) does not actually hold
for every `access_request` in practice — only for the ones a well-behaved client
happened to raise with `projectId` set.

**This does not block Task 2** — the function as designed is strictly more correct
than today's check for every case, including this one (today, *any* `project_admin`
anywhere in the tenant can decide it; after the fix, only one bound within the
correct business unit can). But it is flagged here, per the brief's instruction,
as a concern the controller should weigh before or alongside Task 3: either (a)
accept the coarser business-unit-wide fallback as the intended floor for any
project-less request regardless of type (simplest, matches the design's own
"generic, not type-specific" philosophy), or (b) tighten `create_request()` to
require `project_id` for `access_request` (and optionally `connector_access`/
`model_credential`) when raised by a role below `project_admin`, closing the gap
at the source rather than relying on the decider-scope fallback to paper over a
missing field.
