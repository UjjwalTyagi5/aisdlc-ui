# Agent Studio, sub-project 2 — Developer sandbox (personal tier) persistence

**Status: approved for implementation, scope revised.** Part of the 6-sub-project Agent
Studio build (see `.superpowers/sdd/2026-08-25-agent-studio/progress.md`). This spec
covers sub-project 2 only.

**Scope revision:** the ledger described this sub-project as "make `scope=user` actually
work end-to-end for both Behavior and Skills (schema, validation, storage,
retrieval)." Research below found the schema needs no change at all (no DB-level CHECK
constraint blocks `"user"`), and the frontend is already nearly ready (it already sends
`chain.userId`, already has full personal-tier UI, and its one `Exclude<ProfileScope,
"user">` cast is on the *propose* path, which is correctly never reachable for a
personal tier — not a bug). The real blocker is entirely backend authorization: today
`create_draft`/`preview`/`publish`/`unpublish` (Behavior) and `create`/`update`/
`delete`/`toggle`/`activate` (Skills) are gated by a **single blanket permission per
route**, held only by `developer` + `org_admin` (writes) or `bu_admin` + `org_admin`
(publish) — regardless of which scope the request targets. Simply adding `"user"` to
`SCOPE_VALUES` without fixing this would let a `developer` write or read **any other
user's** personal default (`scope_id` is caller-supplied, unchecked), which is a
straight-up cross-user data leak/write, not a minor gap. Making the personal tier
persist safely therefore requires the write path to become scope-aware. This spec
covers exactly that — and only that; it deliberately does **not** touch the equivalent,
larger gap for the org/workspace/project tiers (see "Out of scope" below).

## Problem

`AgentProfile.scope` / `AgentSkill.scope` / `AgentSkillToggle.scope` accept `"user"` at
the frontend type level (`ProfileScope` already includes it, full personal-tier UI
already exists) but the backend's `_validate_scope()` (duplicated identically in
`agent_profiles.py` and `agent_skills.py`, both reading the same `SCOPE_VALUES` tuple)
rejects it with a 422. A personal draft can never actually be saved. This blocks the
"developer sandbox" journey entirely: a contributor, developer, BA, architect, QA
engineer, security engineer, DevOps engineer, data engineer, or scrum master — anyone
except the two governance-only roles (org_admin, bu_admin, per PRD §14.8 and the
already-confirmed `canPublishAtTier` rule) — should be able to save their own personal
default behavior/skills, but none of them can today.

## Existing state (verified by reading the code)

- **No DB migration needed.** `agent_profiles`/`agent_skills`/`agent_skill_toggles`
  all define `scope` as `String(16)` with **no CHECK constraint** (unlike
  `role_bindings.scope_kind`, which does have one) — `0001_baseline.py`. `scope_id` is
  already a nullable `UUID` column on all three tables, which is exactly the type a
  user id needs.
- **Frontend is already ready.** `getAgentProfilesSummary`/`createAgentProfileDraft`/
  `previewAgentProfile` in `lib/api/agent-profiles.ts` already type `scope` as the bare
  `ProfileScope` (includes `"user"`) and already send `chain.userId` on every summary/
  preview call. The one `Exclude<ProfileScope, "user">` cast, in
  `proposeAgentProfilePublish`'s input type and its one call site
  (`behavior-tab.tsx:252`), is on the **propose** flow — correctly unreachable for
  `scope="user"` already, since `ScopeContext.canPropose` is always `false` at the
  personal tier and the backend's `propose()` handler already 422s
  `target.scope == "user"` defensively (`agent_profiles.py:546-555`, `NOT_A_SHARED_TIER`).
  This is not a bug to fix. `SkillsTab` (post sub-project 1) already threads
  `scopeContext.scope`/`scopeId` generically, so it needs no frontend change either —
  it will "just work" at `scope="user"` the moment the backend accepts it.
- **The real gap is backend authorization, not backend validation.** Confirmed by
  reading `shared/authz/permissions.py`'s `_ROLE_PERMISSIONS`: `skill:edit` (gates
  Behavior's `create_draft`/`preview`/`propose` and Skills' `create`/`update`/`delete`/
  `toggle`) is held by **only `developer`** (plus `org_admin` via the `admin:*`
  wildcard) — `bu_admin`, `project_admin`, `ba`, `architect`, `qa`,
  `security_engineer`, `devops_engineer`, `data_engineer`, `scrum_master`, and
  `contributor` hold neither `skill:edit` nor any equivalent. `workspace:manage`
  (gates `publish`/`unpublish`/Skills' `activate`) is held only by `bu_admin` (+
  `org_admin`). Both are single, scope-blind permission strings checked entirely at
  the FastAPI `Depends()` level, before the handler (and therefore before `scope` is
  even inspected) runs. There is no mechanism today by which a non-developer,
  non-org_admin role could write to a personal scope even if `_validate_scope` allowed
  `"user"` through.
- `ancestor_chain(scope, scope_id, workspace_id)` (`agent_profiles.py`, shared by
  `skill_store.py`) only knows about `org`/`workspace`/`project` — it has no concept of
  a `user` scope's own ancestors (project → workspace → org), so a personal tier's
  inherited-content resolution (sub-project 1's `inherited_from`/`origin_scope`
  machinery) would silently show nothing inherited for anyone's personal tier, even
  though the whole point of the cascade is that a personal default still inherits from
  whatever project/BU/org tier the person is currently in.
- `project_scope.py`/`read_scope.py` already provide reusable, tested primitives for
  "does this user administer/belong to this workspace/project" (`administered_workspace_ids`,
  `visible_project_ids`, `is_org_wide`, `live_binding`) — reused below rather than
  reinvented.

## Design

### 1. `SCOPE_VALUES` gains `"user"`

`SCOPE_VALUES: tuple[str, ...] = ("org", "workspace", "project", "user")` in
`agent_profiles.py` (imported by `agent_skills.py` — one change point). `_validate_scope`
(both copies) requires `scope_id` for `"user"` too, not just `"workspace"`/`"project"` —
a personal row always needs an owning user id, exactly like a workspace/project row
always needs its scope id.

### 2. Scope-aware write authorization (the actual fix)

Each of the affected routes currently reads:

```python
@agent_profiles_router.post("/draft", dependencies=[Depends(require_permission("skill:edit"))])
```

This becomes an **in-body check**, following the exact precedent already established
in this codebase for a permission that depends on data unavailable until the request
is parsed (`signals.py`'s `_check_permission_for_phase`, documented in
`shared/authz/dependency.py`'s `_SIGNALS_IN_BODY_PROTECTED_PATHS` comment — same
pattern, applied here to `scope` instead of pipeline phase). The route-level
`Depends()` drops to the router's existing floor (`artifact:view`, already applied at
router-construction time for both routers — no change needed there, and the D-05 boot
scan stays satisfied because that floor dependency still carries the
`require_permission` sentinel). A new helper function, colocated with `ancestor_chain`
in `agent_profiles.py` and imported by `agent_skills.py` exactly like `SCOPE_VALUES`
already is:

```python
def assert_can_write_agent_scope(
    perms: list[str], role: str | None, scope: str, scope_id: str | None,
    actor_user_id: str, *, action: Literal["draft", "publish"],
) -> None:
    """Scope-aware authorization for an Agent Studio write. Raises 403.

    For org/workspace/project: UNCHANGED from today's behavior — exactly the same
    permission string that already gated the route at the Depends() level, just
    checked one line later, after `scope` is known. draft/preview need "skill:edit";
    publish/unpublish (and Skills' activate) need "workspace:manage". Zero behavior
    change for the three existing tiers — same permission, same actors, same denials.

    For user: a personal default is self-service and outside the cascade everyone
    else inherits from (mirrors the existing `propose()` "NOT_A_SHARED_TIER" reasoning
    for the same tier). Allowed when the caller's role is neither "org_admin" nor
    "bu_admin" (the two governance-only roles, PRD §14.8 — never run an agent, so a
    personal default they set could never take effect) AND `scope_id` equals the
    caller's own user id. Every other combination — writing someone ELSE'S personal
    scope, or an org_admin/bu_admin writing their own — is denied. This is the
    server-authoritative twin of `canPublishAtTier` in `frontend/lib/governance.ts`,
    whose own docstring already says it's meant to be "shared by the client gate and
    BOTH server runtimes" — this closes that gap for the personal tier specifically.
    """
```

Applied at each call site:

- Behavior `create_draft`/`preview`: `action="draft"`, scope/scope_id from `body`.
- Behavior `publish`/`unpublish`: `action="publish"`, scope/scope_id from the already-
  loaded `target` row (checked after `_load_or_404`, mirroring how `propose()` already
  reads `target.scope` post-load).
- Skills `create_skill`/`update_skill`/`delete_skill`/`toggle_skill`: `action="draft"`,
  scope/scope_id from `body`.
- Skills `.../activate/{version}`: `action="publish"`, scope/scope_id from the loaded
  skill row.

`propose()` needs no change — it already 422s `scope == "user"` before this check would
ever run, and its own permission gate (`skill:edit`) becomes, by the same in-body
pattern, "can draft at the target's scope" — which for a real propose (org/workspace/
project only, by the existing early-exit) is unchanged from today.

### 3. `ancestor_chain` gains a `user` case

```python
if scope == "user":
    chain: list[tuple[str, str | None]] = []
    if project_id:
        chain.append(("project", project_id))
    if workspace_id:
        chain.append(("workspace", workspace_id))
    chain.append(("org", None))
    return chain
```

Requires threading a `project_id` parameter alongside the existing `workspace_id` one
— `ancestor_chain`'s signature gains `project_id: str | None = None`, defaulted so
every existing call site (which never resolves a `user`-scope chain today) is
unaffected. The two real callers that need the personal tier's chain
(`get_summary` in `agent_profiles.py`, `list_skills` in `agent_skills.py`) already
receive `project_id` as a query param from the frontend's `chain.projectId` — pass it
through.

### 4. `_scope_filters` gains a `user` case

Identical shape to the existing `project`/`workspace` branches — `scope_id` is
required and filtered on exactly like today's project/workspace rows. No special
casing needed beyond `SCOPE_VALUES` already accepting `"user"`.

## Role journeys (this piece only)

- **Contributor**: can now save a personal default for any agent — the first tier they
  can write to at all (they own none of org/workspace/project). Their rail shows
  "Inherited from Project" (or BU, or Org) exactly like sub-project 1 already renders
  for the shared tiers, now one step longer down to their own personal draft.
- **Developer / BA / Architect / QA / Security Engineer / DevOps Engineer / Data
  Engineer / Scrum Master**: same — a personal sandbox that inherits from whatever
  project they're currently in, saves and publishes with no approval needed (self-
  service, per the existing `canPublishAtTier` rule this backend change now actually
  enforces).
- **Project Admin**: same personal-tier access as everyone else, *in addition to*
  their existing project-tier ownership — the two are independent (a Project Admin's
  personal sandbox is still theirs alone; it does not become the project default).
- **BU Admin / Org Admin**: still cannot write a personal scope (write attempt now
  actually 403s instead of the previous blanket 422-for-everyone) — unchanged from the
  already-confirmed rule; the only change is that the enforcement is now real instead
  of a UI-only suggestion in the mock server.

## Out of scope for this sub-project

- **The equivalent gap for org/workspace/project tiers** — `skill:edit`/
  `workspace:manage` remain single blanket permissions with no ownership-of-*this
  specific tier* awareness (e.g. a `project_admin` still cannot draft/publish their
  OWN project's tier today, because they hold neither permission; a `bu_admin` could
  still technically draft at `scope="project"` if they held `skill:edit`, which they
  don't, so it's latent rather than exploitable today). This is real, and it is what
  makes "propose one tier up, never skip" not actually enforceable server-side yet —
  but it's a materially larger change (needs a full per-role home-tier mapping, reuse
  of `assert_can_administer_project`/`administered_workspace_ids` for project/workspace
  membership, and touches the publish path for all three shared tiers, not just one).
  It naturally belongs to **sub-project 3 (Skills promotion parity)**, which is already
  about building out real propose/promote mechanics — fixing shared-tier ownership
  enforcement in isolation here, without also building the promotion workflow it
  exists to support, would be solving half the problem twice. Flagging explicitly so
  it isn't lost: this is a known, real, non-blocking gap, not an oversight.
- Any change to the promotion/propose workflow itself — untouched (`propose()`'s
  existing `NOT_A_SHARED_TIER` guard for `scope="user"` needs no change and is left
  as-is).
- Evaluation gating, import screening, audit completeness — separate sub-projects.

## Testing

- Backend, live-DB (mirroring `test_model_grants.py`'s per-test random tenant
  convention): `_validate_scope("user", None)` still 422s (scope_id required);
  `_validate_scope("user", <uuid>)` passes. `assert_can_write_agent_scope` table-driven
  across the full role × scope × action matrix: for org/workspace/project, every case
  matches TODAY'S actual permission-check outcome exactly (regression-proof — same
  roles pass/fail as before this change) for both `action="draft"` and
  `action="publish"`; for user, only a non-org_admin/non-bu_admin role writing their
  OWN `scope_id` passes for both actions, every other combination (wrong scope_id,
  org_admin, bu_admin) 403s. Route-level tests: a `developer` (or `contributor`, `ba`,
  etc.) can `POST /agent-profiles/draft` and `.../publish` with `scope="user",
  scope_id=<their own id>` end-to-end and read it back via `GET /agent-profiles/summary`;
  the identical request with someone ELSE'S id 403s; `bu_admin`/`org_admin` attempting
  their own personal scope 403s. Equivalent round-trip for
  `POST /agent-skills` create/update/toggle/delete + `GET /agent-skills` at
  `scope="user"`. `ancestor_chain("user", uid, workspace_id=W, project_id=P)` returns
  `[("project", P), ("workspace", W), ("org", None)]`; each of `project_id`/
  `workspace_id` individually omitted degrades gracefully (matches the existing
  project-without-workspace_id precedent from sub-project 1).
- Frontend: no new component tests expected (per the "already ready" finding above) —
  a light smoke test confirming `behavior-tab.tsx`/`skills-tab.tsx` render without
  error when `scopeContext.scope === "user"` and a save/publish round-trip succeeds
  against the (now-accepting) mocked API, to catch any accidental frontend assumption
  that personal tier never persists.
