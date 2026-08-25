# Agent Studio, sub-project 1 — Inheritance visibility

**Status: approved for implementation.** Part of a 6-sub-project Agent Studio build
(see `.superpowers/sdd/2026-08-25-agent-studio/progress.md` for the full list and
already-gathered context). This spec covers sub-project 1 only.

## Problem

Agent Studio resolves both Behavior and Skills on a cascade — Organization → Business
Unit → Project → Personal, most-specific tier wins. Today, when a tier has no override
of its own, the UI cannot tell the difference between "nothing has ever been set,
anywhere, this is the raw vendor default" and "an ancestor tier (Org, or a BU) has
already set something, and I'm currently inheriting it silently." A Business Unit Admin
opening their own tier has no way to see that the Organization Admin already configured
a default behavior or added a skill — they can only discover this by accident, e.g. by
testing a run and noticing unfamiliar instructions.

This directly blocks the "top-to-bottom" journey the org admin/BU admin/project admin
ownership model depends on: an Org Admin sets an org-wide default expecting it to be
visible to everyone below; today it's invisible until someone stumbles onto it.

## Existing state (verified by reading the code)

- **Behavior** (`agent_profiles` table / router): the frontend's Zod schema already
  reserves a field for this — `AgentProfileSummaryEntry.inherited_from` — but the
  backend's `build_agent_summary()` never populates it (it only reports this exact
  scope's own rows), and `agent-rail.tsx` never reads it. When a tier has no active
  version, the rail shows a bare "default" label with no indication of whether that
  means "vendor base" or "inherited from Org."
- **Skills** (`agent_skills` / `agent_skill_toggles` tables, `skill_store.py`): worse —
  `list_skills_merged()` queries ONLY the exact scope requested
  (`AgentSkill.scope == scope, AgentSkill.scope_id == sid`). It never looks at ancestor
  tiers at all. A BU with no custom skills of its own sees zero custom skills in its
  management list, even if the Org has three that actually apply here at runtime (the
  separate runtime resolver, `skill_runtime.py`, presumably does cascade correctly for
  actually running an agent — this spec does not touch that; it only fixes the
  *management/visibility* list, which is a different code path that never learned the
  same trick).
- `AgentSkillToggle` already exists specifically so a scope can enable/disable a skill
  it didn't author (per its own docstring) — this spec reuses that mechanism as-is; it
  does not change toggle semantics.

## Design

### 1. Backend — Behavior (`agent_profiles.py`)

`get_summary` gains two optional query params, `workspace_id` and `project_id`,
matching the same `chain` shape the frontend already tracks while drilling in. These
let the endpoint resolve the full ancestor chain for the requested tier in one call:

- `scope=org` → chain is just `[org]`.
- `scope=workspace` → chain is `[org, workspace_id]`.
- `scope=project` → chain is `[org, workspace_id, project_id]` (workspace_id required
  — a project's ancestor chain needs its parent BU, matching what the frontend already
  resolves before it can render a project's tier at all).
- `scope=user` → out of scope for this sub-project (personal tier has no persistence
  yet — that's sub-project 2). `get_summary` continues to reject `scope=user` exactly
  as it does that today (any four-hundred-family error is fine; this endpoint has
  never accepted "user").

`build_agent_summary` changes: fetch all active rows across the full chain in one
query (`AgentProfile.scope.in_(...)` with matching scope_ids, `is_active=True`), keyed
by scope. For each agent: if the requested tier has its own active row, behave exactly
as today (`inherited_from: null`). If not, walk the chain from most-specific ancestor
to least (project's parent workspace, then org) and report the nearest active row's
scope as `inherited_from`, with its content surfaced the same way `active` already is
today (so the frontend can render it without a second round-trip).

No new tables, no migration — this is a query and response-shape change only.

### 2. Backend — Skills (`skill_store.py`)

`list_skills_merged` gains the same `workspace_id`/`project_id` chain params. Custom
skills are now fetched across the full chain (not just the exact scope), grouped by
`skill_key`. For each `skill_key`, the most-specific tier's row wins (project beats
workspace beats org) — same precedence rule Behavior already uses, applied here for
the first time. The winning row is returned with a new `origin_scope` field (distinct
from the existing `origin: "vendor"|"custom"` field) naming which tier it actually
came from. When `origin_scope` differs from the requested `scope`, the item is
inherited; the frontend uses this to decide whether to show an "Override" action.

Vendor skills are unaffected — they have no scope of their own, so this concept
doesn't apply to them.

### 3. Frontend

- `agent-rail.tsx`: replace the bare "default" label with a scope-aware badge —
  "Inherited from Org" / "Inherited from Business Unit" — whenever `inherited_from` is
  set and there's no local active version. Own-tier active versions render exactly as
  today (`v{n}` badge).
- `behavior-tab.tsx` (or wherever the selected agent's content renders): when showing
  an inherited config, render it read-only with a clear "Override at my tier" button.
  Clicking it seeds `createAgentProfileDraft` with the inherited content pre-filled
  (prompt_prepend/prompt_append/output_contract_extra) rather than blank fields — the
  admin edits from a known-good starting point instead of retyping from scratch.
- `skills-tab.tsx`: each skill item shows its `origin_scope` when it differs from the
  current tier ("From Organization" / "From Business Unit"). Toggle controls already
  work unchanged (existing `AgentSkillToggle` mechanism). Add an "Override" action next
  to inherited custom skills — creates a new custom skill at the current tier via the
  existing `POST /agent-skills` with the same `skill_key`, `display_name`,
  `description`, `when_to_use`, `body` pre-filled from the inherited row. Confirmed
  safe: `create_skill`'s duplicate-key check calls `get_skill_detail` with the target
  scope/scope_id explicitly, so it only collides against the tier being written to,
  not ancestor tiers — creating a same-key override at a new tier already works today.

### 4. Role journeys (this piece only)

- **Contributor (Personal tier)**: sees "Inherited from Org" / "Inherited from
  Business Unit" / "Inherited from Project" on their own rail exactly like everyone
  else — the chain now includes their tier too, one step longer. (Saving a personal
  override is sub-project 2; this piece only makes the *visibility* correct.)
- **Project Admin**: opens their project's tier, immediately sees which of the 8
  agents already have an Org or BU default active, without clicking into each one.
  Overriding one is unchanged (existing draft/publish flow) except the draft now
  starts pre-filled from the inherited content.
- **BU Admin**: same, one tier up — sees Org defaults on their rail.
- **Org Admin**: sees nothing inherited (org is the top of the chain) — unaffected by
  this piece, confirming their tier's rail behavior is already correct today.

## Out of scope for this sub-project

- Personal/Developer sandbox persistence (`scope="user"` actually saving) — sub-project 2.
- Any promotion/propose workflow changes — untouched.
- Evaluation gating, import screening, audit completeness — separate sub-projects.

## Testing

- Backend: unit tests for the chain-walk logic in `build_agent_summary` (own tier
  active → no inheritance; own tier empty, workspace has one → inherited from
  workspace; own tier and workspace both empty, org has one → inherited from org; all
  three empty → `inherited_from: null`, matching today's vendor-base-only case).
  Equivalent table-driven tests for `list_skills_merged`'s per-`skill_key` precedence.
  Route-level tests confirming the new query params are accepted and threaded through.
- Frontend: `agent-rail.tsx` renders the right badge for each `inherited_from` value;
  the "Override at my tier" action calls `createAgentProfileDraft` with the inherited
  content as initial values (not blank); skills-tab shows `origin_scope` badges and the
  override action creates a same-key skill at the correct scope.
