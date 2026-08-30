# Approval Workflow Sub-Project A — Effect Reachability & Consistency

**Status:** design, self-reviewed, ready for planning.

## 1. Problem statement

The platform has two correctly-separated approval lanes (PRD §33.2): agent-gate
**Approvals** (sideways, to the owning role) and upward-escalating **Requests**
(Project Admin → BU Admin → Org Admin). Both exist and are largely sound. This
sub-project is about a narrower, more concrete defect inside the Requests lane:
**for most request types, approving a request does not reliably do the thing it
was approved to do.**

Two different failure shapes produce that symptom, and this sub-project fixes
both:

1. **No real effect is wired at all** — `mcp_server`, `agent_access`,
   `model_credential`, `access_request`, `user_onboarding` sit in
   `_DECISION_IS_THE_OUTCOME` (`shared/governance/effects.py`). Approving
   records agreement; nothing is granted. `role_assignment` is worse — clicking
   Approve raises `EffectNotAvailable` and surfaces as an error.
2. **An effect exists and looks correct in isolation, but is UNREACHABLE from
   any real user flow** — `connector_access` and `model_provider_access` both
   have real, tested `_apply_*` functions, but every UI path that raises them
   goes through the generic `POST /governance-approvals` route, whose
   `RequestCreateIn` schema (`shared/routers/governance_requests.py:51-70`)
   carries no field for WHICH connector or WHICH provider is being asked for —
   only free-text `title`/`description`, plus `phase` (used solely by
   `agent_access`, and even that one isn't fully wired — see §4). Approving
   either type today produces `EffectNotAvailable` — "This request names no
   connector to grant" / "That provider no longer exists" — every time, for
   every request, because the payload the effect function reads was never
   populated at raise time.

Failure shape 2 is the one a static read of `effects.py` cannot catch — it
looks done. It was found by tracing an actual UI button
(`components/app/model-availability-card.tsx`'s `RequestAccessButton`,
`app/(app)/integrations/page.tsx`'s per-tile `RequestAccessButton`s) all the
way to `_apply_connector_access`/`_apply_model_provider_access` and confirming
neither ever receives the data it requires. **This is the central finding of
this sub-project**, and the reason its scope is "make effects actually
reachable," not "write five effect functions."

## 2. What already exists (survey, do not rebuild)

- **`RequestCreateIn`/`RequestCreateInput`** (backend `governance_requests.py`,
  frontend `lib/schemas/governance-approval.ts:288`) — the schema every
  client-raised request passes through. Already has one precedent for a typed,
  content-not-routing extra field: `phase`. Its own docstring states the
  design rule this sub-project extends: "a client naming its own ask is just
  the ask" (as opposed to naming its own approver, which is the thing the
  schema is built to prevent). New target fields belong here, following the
  same shape.
- **`service.create_request`'s `phase` handling**
  (`shared/services/governance_requests.py:315,380-381`) — the established
  pattern for turning a typed kwarg into stored data: `payload = {**(payload
  or {}), "phase": phase} if phase else payload`. Any new target field
  follows this exact shape — merged into `payload` inside the service
  function, never assembled ad hoc at each call site.
- **`target_ref`** — a top-level column on `governance_requests`, distinct
  from `payload`. Already used by every SYSTEM-RAISED type
  (`cross_bu_assignment`, `project_creation`, `project_archive`,
  `agent_default_*`), each of which passes an explicit `target_ref=` when
  calling `service.create_request` directly (bypassing the generic router).
  `_apply_model_provider_access` reads from `target_ref`; `_apply_
  connector_access` reads from `payload.targetId`. Both mechanisms are
  legitimate and already exist — the gap is that a CLIENT-raised request
  (going through the generic `POST /governance-approvals` route) has no way
  to populate either one, for any type except `phase`.
- **`set_project_selection`** (`shared/services/model_grants.py:440`) — the
  manual write a Project Admin already performs to add a (provider, model_id)
  to their project's selection. This is the exact operation `model_credential`
  approval should perform — the write already exists and is tested; this
  sub-project calls it from a new effect function, it does not reinvent it.
- **`_apply_connector_access`'s two branches** (`effects.py:633-784`) — unit-tier
  (`integration_grants`, org-admin-only) and project-tier
  (`project_connector_access`, validated against the connector capability
  manifest). Both branches are already correct and tested via direct calls —
  only the payload that reaches them is missing. Same file's existing
  `_apply_model_provider_access` (status flip on `model_providers`) is
  similarly correct in isolation.
- **`complete_role_assignment`** (`shared/services/governance_requests.py:742`)
  — already auto-closes a `role_assignment` request the moment
  `PATCH /workspaces/{id}/members/{userId}` actually assigns the role. The
  request lane's `EffectNotAvailable` on direct-approve is not a missing
  feature; it is a deliberate refusal because there is nothing DIRECT for
  Approve to do — the real action happens on Members. The bug is purely that
  the Approvals queue's Approve button doesn't know this and throws instead of
  guiding the approver to the right place.
- **`RequestAgentAccessDialog`** (`components/app/request-agent-access-dialog.tsx`)
  — the one dedicated, payload-carrying raise entry point that already exists.
  Confirms the pattern this sub-project extends to the other types: a small,
  purpose-built dialog (not the generic `RaiseRequestDialog`) that knows
  exactly what it's asking for and sends it as a real field.

## 3. Scope — what this sub-project delivers

### 3.1 Plumbing: a real target, end to end

Add typed optional fields to `RequestCreateIn`/`RequestCreateInput`, following
the `phase` precedent exactly (content, never routing, never an approver):

- `targetId: Optional[str]` — the specific connector kind, MCP server row id,
  or model provider id being asked for. Consumed by `connector_access`,
  `mcp_server`, `model_provider_access`.
- `providerModel: Optional[{provider: str, modelId: str}]` (or an equivalent
  shape agreed at plan time) — for `model_credential`, which needs BOTH a
  provider and a model id, not just one opaque id.
- `onboardEmail: Optional[str]` — for `user_onboarding`, the email of the
  person being asked about. Without this, "approve" cannot know who to
  onboard, and the current no-op behavior is the only honest option.

Each new field is merged into `payload` inside `service.create_request`,
exactly like `phase` is today — never assembled per-call-site, so every raise
path (generic dialog, any future dedicated dialog) gets it for free once
supplied.

### 3.2 Wire every "Request X" entry point to populate its target

Every place in the frontend that raises one of these five types must be
updated to actually send the field(s) above, not just a human-readable title:

- `RequestAccessButton` usages on `model-availability-card.tsx` (both
  `model_credential` and `model_provider_access`) — currently only sends
  `{type, title, description}`. Needs the real provider/model id.
- `RequestAccessButton` usages on `app/(app)/integrations/page.tsx` (both
  `connector_access` tiles and `mcp_server` tiles/section button) — needs the
  connector `kind` string or MCP server row id.
- `RequestAgentAccessDialog` — already sends `phase`; verify the STAGE-TWO
  approval path (agent owner) actually has enough to write the grant (see
  3.3) rather than assuming it's already complete.
- `user_onboarding`'s raise path(s) — locate every place this type is
  currently raised (generic dialog only, per the audit) and give it a real
  email field, or a dedicated small dialog matching
  `RequestAgentAccessDialog`'s shape if no page-embedded entry point exists
  yet.

`RaiseRequestPrefill` (the shared prop these dialogs pass down) gains whatever
new optional fields are needed to carry this through without breaking its
existing "locked type" contract.

### 3.3 Real effects, once the target is reachable

With §3.1-3.2 in place, give each of these a real `_apply_*` function in
`effects.py`, moving it out of `_DECISION_IS_THE_OUTCOME`:

- **`connector_access`** — no new effect needed; `_apply_connector_access`
  already exists and is correct. This item is entirely "make the payload
  reach it" (§3.1-3.2). Verify live, end to end, that it now actually grants.
- **`model_provider_access`** — same: existing effect, needs a real
  `target_ref` from the client instead of the workspace-id fallback it
  silently receives today.
- **`model_credential`** — new effect, calling `set_project_selection` (or
  `assign_provider_to_project`, whichever the plan determines is the correct
  mirror of the manual flow) with the provider/model the request named.
- **`mcp_server`** — new effect, mirroring `_apply_connector_access`'s shape:
  org-tier permits+classifies (per PRD §34.4, `kind == "mcp"` already flows
  through the SAME function today when raised as `connector_access` with
  `kind: "mcp"` — the plan must determine whether `mcp_server` becomes a thin
  wrapper around the existing `_apply_connector_access(kind="mcp")` path
  rather than a parallel implementation, given GodOfDecay's recent commit
  already unified their frontend treatment).
- **`agent_access`** — new effect at FINAL decision (stage two, the agent
  owner's sign-off): write the requested `phase` into the requester's
  `role_bindings.extra_agents` for that project, mirroring the existing manual
  "grant extra agent access" admin action (PRD §43.2 step 3).
- **`user_onboarding`** — new effect calling the same onboarding path
  `POST /onboarding` already uses, with the email the request now carries.

### 3.4 `access_request` — considered and rejected

`access_request` is the platform's explicit catch-all/generic type (routing.py's
own comment: "the catalogue's `other`" sibling). It has no addressable single
target by design — a person can describe any kind of access in prose. Giving it
a "real effect" would mean either (a) inventing a fake generic grant mechanism
that doesn't correspond to anything, or (b) parsing free text to guess what to
grant, which is worse than doing nothing. **Ruling: `access_request` stays in
`_DECISION_IS_THE_OUTCOME`, unchanged.** This is a deliberate exception to the
"make all real effects" decision, not an oversight — recorded here so it reads
as a considered choice rather than a type quietly skipped.

### 3.5 `role_assignment` — UX fix, not an effect

Per §2, the correct behavior already exists (`complete_role_assignment`); only
the Approvals queue's UI is wrong. Fix: the request detail view for a
`role_assignment` request replaces the generic Approve/Reject buttons with a
clear "Assign the role on Members" action that navigates there (or, if the
plan finds a clean way, opens the assign-role dialog inline with the request's
proposed role/workspace pre-filled) — never a bare error toast.

### 3.6 Live verification — every type, not just the five

Because the central finding of this sub-project is "static reading missed a
real bug," this sub-project's testing is NOT limited to the five/two types
above. Before any fix work, and again after, every request type in
`routing.REQUEST_TYPES` gets a genuine end-to-end live test: raise it as the
correct role against the running backend + real database (not a mocked
client), decide it as the correct role, and directly query the database to
confirm the real-world effect happened (or, for `_DECISION_IS_THE_OUTCOME`
types, confirm the status flip and nothing else). This is how `connector_access`
and `model_provider_access`'s reachability bug was found, and it is the only
reliable way to confirm the rest of the system — including the six
already-"working" system-raised types — has no sibling bugs of the same shape.

## 4. Out of scope (belongs to sub-projects B/C/D, already scoped and approved)

- Break-glass / time-bound elevation (§44.4) — sub-project D.
- Mandatory-checkpoint bypass verification (can `admin:*` skip a mandatory
  sign-off?) and the two-delivery-roles self-approval edge case — sub-project B.
- New page-embedded "Request" buttons where none exist at all (Cost & Budget)
  — sub-project C. NOTE: this sub-project's own scope (§3.2) touches the
  EXISTING buttons on Model Management and Integrations to fix their payload,
  which is a different, narrower change than adding a new button to a page
  that has none.

## 5. Testing approach

Every task in the implementation plan ends with a live test against the real
running dev stack (backend `127.0.0.1:8001`, the real local Postgres), using
this repo's established live-DB convention (`get_db_session_for_tenant` + raw
`text()` inserts + a random UUID tenant per test) for anything not already
covered by `tests/test_governance_requests.py`. A fix is not "done" on a
green `pytest` run alone if the live end-to-end trace (raise → decide →
database row) was not independently confirmed — this is the exact discipline
that was missing the first time `connector_access`/`model_provider_access`
were marked working.

## 6. Self-review notes

- **Placeholder scan**: none found — every requirement above names the
  specific file/function it touches or creates.
- **Internal consistency**: §3.3's `mcp_server` item flags a real open
  question (thin wrapper vs. parallel implementation) rather than pretending
  a decision was already made — this is intentional; it is a plan-time
  decision, not a spec-time one, because it depends on reading
  GodOfDecay's actual commit diff first.
- **Scope check**: focused enough for one implementation plan. §3.1-3.2 (the
  plumbing) and §3.3 (the effects) are sequenced as they are because 3.3 is
  provably impossible without 3.1-3.2 first — this ordering must be preserved
  in the plan's task breakdown.
- **Ambiguity check**: `providerModel`'s exact shape (§3.1) is deliberately
  left for plan time, once `set_project_selection`'s real signature is read in
  full — the spec commits to WHAT is needed (a provider and a model id) not
  the exact wire shape.
