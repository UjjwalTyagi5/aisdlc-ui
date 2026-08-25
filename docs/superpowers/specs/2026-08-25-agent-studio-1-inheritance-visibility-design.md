# Agent Studio, sub-project 1 — Skills cascade + inheritance visibility

**Status: approved for implementation, scope revised.** Part of a 6-sub-project Agent
Studio build (see `.superpowers/sdd/2026-08-25-agent-studio/progress.md` for the full
list, already-gathered context, and the ruling log explaining the scope revision below).
This spec covers sub-project 1 only.

**Scope revision:** research turned up that Skills is not yet cascade-aware at all —
`agent-editor.tsx` has an explicit comment marking it "a separate, BU-scoped system
(out of scope for the cascade)," and `SkillsTab` hardcodes `workspace` as its only
scope. Inheritance visibility is meaningless for a system that can't exist at more
than one tier, so **making Skills participate in the same Org → Business Unit →
Project → Personal cascade Behavior already has is now part of this sub-project**,
ahead of (and prerequisite to) the inheritance-visibility work itself. This spec covers
both. It does not touch Behavior's own cascade (already correct) beyond the
inheritance-visibility fixes below.

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

### 0. Make Skills cascade-aware (new prerequisite)

The backend (`agent_skills.py`, `skill_store.py`) already accepts `scope`/`scope_id`
generically — `SCOPE_VALUES` is imported from `agent_profiles.py` and already covers
org/workspace/project. **The backend is not the blocker.** The blocker is entirely in
the frontend, which never threads the drilled-into tier down to `SkillsTab`:

- `agent-editor.tsx` passes `SkillsTab` only `workspaceId`/`workspaceName` (hardcoded
  from `scopeContext.workspaceId`) instead of the full `scopeContext` `BehaviorTab`
  already receives. Change: pass `scopeContext` through unchanged, same as
  `BehaviorTab` does.
- `SkillsTab` hardcodes `const SCOPE = "workspace" as const`. Change: derive `scope`/
  `scopeId` from `scopeContext` (`scopeContext.scope`, `scopeContext.scopeId`), exactly
  mirroring how `BehaviorTab` already does it. Every call site inside `SkillsTab` that
  currently passes `SCOPE, workspaceId` (list/create/update/toggle/delete/versions)
  switches to `scope, scopeId`.
- `SkillsTab`'s permission model is currently a flat, non-cascade check:
  `skillsCanManage = session ? can(session.role, "project:update") : false` in
  `agent-editor.tsx`, ignoring which tier is being viewed entirely — a Project Admin
  looking at the Org tier's skills would incorrectly see themselves as able to manage
  it. Change: reuse `scopeContext.isOwner` (own-tier direct write) exactly as
  `BehaviorTab` does — `canManage` becomes `scopeContext.isOwner`. Skills promotion
  (propose-when-not-owner) is explicitly sub-project 3, not this one — for now, a
  non-owner viewing a tier they don't own gets the existing read-only treatment
  (`canManage=false`), same UX shape `BehaviorTab` already has for `!isOwner &&
  !canPropose` today, just without a propose path yet (that's added in sub-project 3).
- Group-header copy ("This Business Unit") and the vendor-vs-custom section labels are
  currently BU-specific text. Change: make the "custom" section header scope-aware
  (`"This {scopeLabel}"` using the SAME `scopeLabel`/`SCOPE_TIER_LABEL`-shaped mapping
  `BehaviorTab` already has — reuse rather than reinvent), so an Org-tier view reads
  "This Organization" and a Personal-tier view reads "This You" → adjust wording to
  "Your personal skills" specifically for the `user` tier, matching how `BehaviorTab`'s
  own `SCOPE_TIER_LABEL` already special-cases `user: "you"` in sentence contexts.

No backend changes are needed for this piece — `_validate_scope`/`SCOPE_VALUES` already
accept org/workspace/project (personal/`user` scope support is sub-project 2, out of
scope here — same as Behavior, `SkillsTab` at the `user` tier is deferred to
sub-project 2 exactly like `BehaviorTab`'s personal tier already defers saving to that
sub-project; this piece only needs Skills to correctly follow whichever of
org/workspace/project tier the cascade is currently drilled into).

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
- `behavior-tab.tsx`: **no changes needed.** Verified while reading the current code —
  it already renders the inherited-config message (lines 315-320: "No {scopeLabel}
  override yet — currently inheriting the {tier} default") whenever `inherited_from` is
  set, and `fieldsFrom()` already seeds the editable fields from `summary.active` —
  which the backend change above will populate with the inherited content the moment
  there's no own-tier active version. The existing "Save draft" flow IS the override
  action; no separate button needed. This piece is backend-only for Behavior.
- `skills-tab.tsx`: each skill item shows its `origin_scope` when it differs from the
  current tier ("From Organization" / "From Business Unit"). Toggle controls already
  work unchanged (existing `AgentSkillToggle` mechanism). Add an "Override" action next
  to inherited custom skills — creates a new custom skill at the current tier via the
  existing `POST /agent-skills` with the same `skill_key`, `display_name`,
  `description`, `when_to_use`, `body` pre-filled from the inherited row. Confirmed
  safe: `create_skill`'s duplicate-key check calls `get_skill_detail` with the target
  scope/scope_id explicitly, so it only collides against the tier being written to,
  not ancestor tiers — creating a same-key override at a new tier already works today.
  This is layered on top of section 0's scope-threading change (same file).

### 4. Role journeys (this piece only)

- **Contributor (Personal tier)**: sees "Inherited from Org" / "Inherited from
  Business Unit" / "Inherited from Project" on their own rail exactly like everyone
  else — the chain now includes their tier too, one step longer. (Saving a personal
  override is sub-project 2; this piece only makes the *visibility* correct.) For
  Skills, a Contributor can now at least *see* their project's/BU's/org's skills in the
  Skills tab for the first time — previously it silently showed only BU-tier skills no
  matter which tier they'd drilled into.
- **Project Admin**: opens their project's tier, immediately sees which of the 8
  agents already have an Org or BU default active, without clicking into each one —
  for both Behavior and, for the first time, Skills. Overriding a Behavior default is
  unchanged (existing draft/publish flow, now pre-filled from the inherited content);
  overriding a Skill uses the new "Override" action, which creates a project-scoped
  copy of the inherited skill.
- **BU Admin**: same, one tier up — sees Org defaults on their rail, and for the first
  time sees Org-level custom skills in their Skills tab (previously invisible: the tab
  only ever showed the exact BU tier, so an Org skill this BU never overrode was
  simply absent from the list with no indication it existed).
- **Org Admin**: sees nothing inherited (org is the top of the chain) for either
  Behavior or Skills — unaffected by the inheritance-visibility half of this piece.
  They DO gain something new from section 0: Skills at the Org tier is now reachable
  at all (today `SkillsTab` cannot render at `scope="org"` since it hardcodes
  `workspace`) — an Org Admin can now author org-wide default skills for the first
  time, which is the "top-to-bottom" journey they specifically asked for.

## Out of scope for this sub-project

- Personal/Developer sandbox persistence (`scope="user"` actually saving) — sub-project 2.
- Any promotion/propose workflow changes — untouched.
- Evaluation gating, import screening, audit completeness — separate sub-projects.

## Testing

- Backend: unit tests for the chain-walk logic in `build_agent_summary` (own tier
  active → no inheritance; own tier empty, workspace has one → inherited from
  workspace; own tier and workspace both empty, org has one → inherited from org; all
  three empty → `inherited_from: null`, matching today's vendor-base-only case, plus
  the existing `test_summary_empty`/`test_summary_no_active_pointer` tests updated for
  the new `inherited_from` key in the returned dict). Equivalent table-driven tests for
  `list_skills_merged`'s per-`skill_key` precedence (own scope wins; own empty,
  workspace wins; own+workspace empty, org wins; toggle nearest-wins across scopes).
  Route-level tests confirming the new query params are accepted and threaded through
  for both `/agent-profiles/summary` and `/agent-skills`.
- Frontend: `agent-rail.tsx` renders the right badge for each `inherited_from` value.
  `skills-tab.tsx`: render tests (React Testing Library, mirroring the existing
  `add-model-dialog.test.tsx` MSW-mock convention) proving (a) it now renders
  correctly at `scope="org"` and `scope="project"`, not just `workspace` (section 0);
  (b) `canManage` follows `scopeContext.isOwner`, not the old flat permission check;
  (c) an inherited custom skill shows its `origin_scope` badge and an "Override"
  action that creates a same-key skill at the current tier. `canPublishAtTier`'s
  existing test file is unaffected — this sub-project doesn't touch ownership rules,
  only which tier Skills can be viewed/edited at.
