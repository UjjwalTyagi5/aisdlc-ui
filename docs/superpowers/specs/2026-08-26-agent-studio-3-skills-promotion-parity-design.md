# Agent Studio, sub-project 3 — Skills promotion parity

**Status: approved for implementation, scope confirmed per sub-project 2's own ruling.**
Part of the 6-sub-project Agent Studio build (see `.superpowers/sdd/2026-08-25-agent-studio/progress.md`).
This spec covers sub-project 3 only.

**Scope confirmation, not revision:** sub-project 2's spec explicitly deferred a real,
confirmed gap here with the ruling *"[fixing shared-tier ownership enforcement] naturally
belongs to sub-project 3, which is already about building out real propose/promote
mechanics — fixing shared-tier ownership enforcement in isolation, without also building
the promotion workflow it exists to support, would be solving half the problem twice."*
This spec follows through on that: sub-project 3 has two parts, done together because
neither is coherent alone —

- **Part A**: give Skills a `propose()` mirroring Behavior's, so a non-owner can draft
  a Skills change and route it for approval instead of being locked out entirely.
- **Part B**: replace the blanket, scope-blind permission strings
  (`skill:edit`/`workspace:manage`) that currently gate ALL org/workspace/project writes
  — for BOTH Behavior and Skills — with real tier-ownership + "propose exactly one tier
  up" enforcement, properly scoped to actual project/workspace membership.

Part A is meaningless without Part B: today only `developer` (draft) and `bu_admin`
(publish, but never draft) hold any relevant permission at all, and `project_admin` holds
neither — the very role that OWNS the project tier cannot touch it. A "propose" button
that only `developer` can ever reach is not promotion parity, it's a permission bug
wearing new UI.

## Problem

Confirmed by reading `backend/shared/authz/permissions.py`'s `_ROLE_PERMISSIONS`:
`skill:edit` (gates every draft-type write) is held by **only `developer`** (+`org_admin`
via the `admin:*` wildcard). `workspace:manage` (gates every publish-type write) is held
by **only `bu_admin`** (+`org_admin`). Concretely, today:

- `project_admin` — the role that per the already-confirmed `AGENT_DEFAULT_OWNER_ROLE`
  ownership model OWNS the project tier — holds neither permission. They cannot draft OR
  publish their own project's Behavior or Skills defaults. They are locked out of the
  one tier that is supposed to be theirs.
- `bu_admin` holds `workspace:manage` (can publish) but not `skill:edit` (cannot draft)
  — cannot create anything to publish in the first place.
- `ba`, `architect`, `qa`, `security_engineer`, `devops_engineer`, `data_engineer`,
  `scrum_master` hold neither — none of them can draft or propose anything, at any tier,
  ever, even to their own project.
- Confirmed via `agent_profiles.py`'s `propose()` (lines 630-725): it performs **zero**
  membership/ownership check on the caller — only the same blanket `skill:edit` gate.
  Any `developer` (or `org_admin`) tenant-wide can propose against ANY project's or
  workspace's tier, including ones they have no binding on whatsoever.
- Skills has no `propose()` at all, and no draft/publish distinction — `create_skill`/
  `update_skill` write and activate in one step, with no "insert but don't go live yet"
  primitive analogous to `AgentProfile`'s `is_active=False` draft rows.

This is not a hypothetical gap. It means the "top-to-bottom, bottom-to-top" RBAC
mechanism the user asked for at the start of this whole effort — each tier's owner
publishes directly, everyone else proposes exactly one tier up, never skipping — does
not actually function server-side today for anyone except `developer` (who can draft-
and-implicitly-propose everywhere, correctly by accident) and `org_admin` (wildcard).

## Existing state (verified by reading the code)

- **`resolve_actor_tier_access`-shaped infrastructure does not exist yet**, but the
  pieces to build it do: `assert_can_administer_project`
  (`shared/authz/project_scope.py:154-186`) shows the exact raw-SQL idiom for "does this
  user hold `project_admin` on this exact project" —
  `SELECT 1 FROM role_bindings rb WHERE {live_binding()} AND rb.scope_kind = 'project'
  AND rb.scope_id = :p AND rb.role_name = 'project_admin'` — but it is `Request`-shaped
  and answers a different question (can this person administer PROJECT SETTINGS, which
  legitimately includes "or administers the parent workspace" — that OR does NOT apply
  to tier ownership, which per `AGENT_DEFAULT_OWNER_ROLE` is strictly `project_admin`,
  never `bu_admin`-by-oversight). `live_binding()` (`shared/authz/read_scope.py`)
  expands to the not-expired/not-deactivated filter every such query needs.
- **`effective_platform_role`/`resolve_platform_role_for_user` (used by sub-project 2)
  are "highest standing wins" GLOBALLY, not scope-aware** — confirmed via
  `platform_role_for`'s own docstring and implementation
  (`shared/authz/effective_role.py:60-92`). A user who is `bu_admin` on Workspace X and
  separately `project_admin` on Project Y resolves to `"bu_admin"` EVERYWHERE, including
  when acting on Project Y. **This function must not be reused as the ownership check for
  Part B** — it would let a `bu_admin` from an unrelated workspace pass a project-tier
  ownership check by accident. A genuine per-resource lookup is required.
- **`projects.workspace_id`** is a direct, non-nullable FK column
  (`migrations/versions/0001_baseline.py:454-469`) — resolving "which workspace does
  project P belong to" is a one-column `SELECT`, already done this way in
  `project_scope.py`'s `resolve_project()` and `can_perform.py`'s `resolve_scope_chain`.
- **`governance_requests.create_request()`** (`shared/services/governance_requests.py:
  289-307`) already determines the approver via static routing
  (`shared/governance/routing.py`'s `GOVERNANCE_APPROVER_ROLE` — `agent_default_org` →
  `org_admin`, `_workspace` → `bu_admin`, `_project` → `project_admin`) and already
  handles the same-role-escalation and self-approval-block cases generically
  (`decide()`, `governance_requests.py:548-555`, a plain identity match on
  `requested_by_id` vs. the decider — works for any request type with zero extra
  plumbing). For `system_raised=True` (what `propose()` sets), the normal
  "can this role raise this type" menu check is skipped entirely by design — the
  platform is filing on the caller's behalf, not offering a picker.
- **The actual publish side-effect for an approved `agent_default_*` request already
  exists and is reusable as-is**: `shared/governance/effects.py`'s `_apply_agent_default`
  (lines 383-441) loads the `AgentProfile` row named by `target_ref`, gathers its
  siblings (same `agent_id`/`scope`/`scope_id`), and calls
  `apply_publish_flip(siblings, row.id)` — the SAME pure function
  (`agent_profiles.py:173-188`, works on ANY objects exposing `.id`/`.version`/
  `.is_active`) that `POST /agent-profiles/{id}/publish` itself calls. **This function is
  reusable UNCHANGED for `AgentSkill` rows** — no new flip logic needed for Skills, only
  a second lookup branch.
- **Skills' `AgentSkill` model is already versioned identically to `AgentProfile`**
  (`shared/models/orm.py:611-647` vs. `575-596`: `scope`, `scope_id`, `version`,
  `is_active`) — but `create_custom_skill`/`update_custom_skill`
  (`shared/services/skill_store.py:465-546`) always insert with `is_active=True`
  immediately; there is no "insert but don't activate" primitive today. Changing this
  globally (making Skills always insert inactive, like `AgentProfile.create_draft` does
  for everyone) would break every existing owner-facing Skills flow shipped in
  sub-project 1 — out of scope and unnecessary. The correct, minimal fix is conditional:
  an OWNER's create/update still activates immediately (zero change to today's behavior
  for owners); a newly-reachable NON-OWNER's create/update (unreachable before Part B,
  since they held no permission to get past the route gate at all) inserts inactive.

## Design

### 1. `resolve_actor_tier_access` — the shared ownership/eligibility lookup

New async function in `agent_profiles.py` (imported by `agent_skills.py`, alongside
`SCOPE_VALUES`/`ancestor_chain`/`assert_can_write_agent_scope`). Opens its own
tenant-scoped session (mirrors `resolve_platform_role_for_user`'s self-contained
pattern from sub-project 2 — used identically by both routers rather than reusing
`agent_profiles.py`'s routes' existing `db` param, for one consistent implementation):

```python
async def resolve_actor_tier_access(
    tenant_id: str, actor_user_id: str, perms: list[str], scope: str, scope_id: str | None,
) -> tuple[bool, bool]:
    """(owns, may_propose) for `actor_user_id` on this EXACT (scope, scope_id) — a
    real per-resource lookup, never the global "highest standing" role
    (`effective_platform_role` is scope-blind and must not be reused here — see
    the spec's "Existing state" section for why).

    owns: may publish/unpublish/activate this tier directly.
    may_propose: may draft-and-file-for-approval at this tier (irrelevant once
    `owns` is True — an owner never needs to propose to themselves).

    org:       owns <-> "admin:*" in perms (org_admin always carries the wildcard;
               no role_bindings lookup needed for a role that IS the wildcard).
               may_propose <-> a live bu_admin binding ANYWHERE in the tenant — org
               is the tenant's one instance, so "one tier up from workspace" needs
               no specific workspace id.
    workspace: owns <-> a live bu_admin binding with scope_kind='business_unit'
               AND scope_id = this workspace, specifically.
               may_propose <-> a live project_admin binding on ANY project whose
               workspace_id = this workspace (one tier up from "some project in
               this BU" — not scoped to one specific project).
    project:   owns <-> a live project_admin binding with scope_kind='project'
               AND scope_id = this project, specifically.
               may_propose <-> ANY live role_binding with scope_kind='project'
               AND scope_id = this project, EXCLUDING role_name='contributor' —
               contributor is explicitly documented elsewhere as "not enough to
               open an agent"; real membership on the project is what earns
               propose access for every other delivery role, whatever it's
               named today or added later.
    """
```

Query shapes (mirroring `assert_can_administer_project`'s exact `live_binding()`
idiom, `project_scope.py:174-183`):

- workspace `owns`: `WHERE {live_binding()} AND scope_kind = 'business_unit' AND scope_id = :w AND role_name = 'bu_admin'`
- workspace `may_propose`: `WHERE {live_binding()} AND scope_kind = 'project' AND role_name = 'project_admin' AND scope_id IN (SELECT id FROM projects WHERE workspace_id = :w)`
- project `owns`: `WHERE {live_binding()} AND scope_kind = 'project' AND scope_id = :p AND role_name = 'project_admin'`
- project `may_propose`: `WHERE {live_binding()} AND scope_kind = 'project' AND scope_id = :p AND role_name != 'contributor'`
- org `may_propose`: `WHERE {live_binding()} AND scope_kind = 'business_unit' AND role_name = 'bu_admin'` (existence only, no scope_id filter)

Each is a `SELECT 1 ... LIMIT 1`, existence-only.

### 2. `assert_can_write_agent_scope` becomes real tier-ownership-aware

Signature changes: gains `tenant_id: str` (needed to call
`resolve_actor_tier_access`), and becomes `async def` (it now does a DB lookup for the
org/workspace/project branch — the `scope == "user"` branch stays exactly as fast/pure
as before, no DB touch). This is a deliberate, ruled behavior change for org/workspace/
project — sub-project 2 explicitly kept that branch's behavior frozen ("zero behavior
change") specifically so THIS sub-project could be the one to change it correctly, once,
with real design behind it rather than as an accidental side effect.

```python
async def assert_can_write_agent_scope(
    tenant_id: str, perms: list[str], role: str | None, scope: str, scope_id: str | None,
    actor_user_id: str, *, action: Literal["draft", "publish"],
) -> None:
    if scope == "user":
        if role is None or role in ("org_admin", "bu_admin"):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not _same_actor(scope_id, actor_user_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, actor_user_id, perms, scope, scope_id,
    )
    if action == "publish":
        if not owns:
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")
```

`has_permission`-based branching is removed from this function entirely for the
shared tiers — ownership is now determined by real per-resource role_bindings, not a
blanket permission string. Both existing parameters stay in the signature because
each is still needed by a DIFFERENT branch: `role` by the unchanged `scope == "user"`
branch (still checking `role not in ("org_admin", "bu_admin")`), `perms` now only to
pass through into `resolve_actor_tier_access`'s org-tier `admin:*` wildcard check
(the org/workspace/project branch itself no longer calls `has_permission` directly).

All 9 existing call sites (4 in `agent_profiles.py`: `create_draft`, `preview`,
`publish`, `unpublish`; 5 in `agent_skills.py`: `create_skill`, `update_skill`,
`delete_skill`, `toggle_skill`, `activate_version`) gain `await` and pass `tenant_id`
(already available in every one of them as a local variable). No route gains a new
`db` dependency — `resolve_actor_tier_access` is self-contained, matching sub-project
2's `resolve_platform_role_for_user` pattern.

### 3. Behavior needs no activation-semantics change

`create_draft` already ALWAYS inserts `is_active=False` for everyone, owner included
(`AgentProfile.create_draft`'s existing behavior, unchanged since before this
sub-project — the owner separately calls `/publish`). Only Skills needs the new
conditional-activation logic (§5) — Behavior's shape already matches the target model.

### 4. Behavior's `propose()` gains a real ownership check

Replace the route-level `Depends(require_permission("skill:edit"))` with the router's
existing `artifact:view` floor (same pattern as every route sub-project 2 touched) plus
an in-body call:

```python
owns, may_propose = await resolve_actor_tier_access(
    tenant_id, perms, target.scope, str(target.scope_id) if target.scope_id else None,
    _user_id(request),
)
if not (owns or may_propose):
    raise HTTPException(status_code=403, detail="Forbidden")
```

Placed after `target = await _load_or_404(...)`, mirroring `publish`'s existing
ownership-check placement (sub-project 2). The existing `scope == "user"` 422
`NOT_A_SHARED_TIER` guard stays first, unchanged. `owns` is deliberately permitted here
too (not `not owns and may_propose`) — the existing docstring already explains why: an
owner calling `propose` on their own tier is harmless-but-pointless (self-approval
blocks it), not something worth a special-cased rejection.

### 5. Skills gains `propose()` + conditional-activation `create`/`update`

`skill_store.py`: `create_custom_skill`/`update_custom_skill` gain an `activate: bool =
True` parameter (default preserves EVERY existing caller's behavior exactly). When
`False`, the inserted row's `is_active` is `False` instead of `True`, and no sibling
deactivation happens (mirrors `AgentProfile.create_draft`'s "insert only, don't touch
anything else" shape — publish/activate is a separate step).

`agent_skills.py`'s `create_skill`/`update_skill`: after the existing
`assert_can_write_agent_scope(..., action="draft")` check, also compute `owns` (from
`resolve_actor_tier_access`, called once, reused for both the authorization check and
the activation decision — no double DB round-trip) and pass `activate=owns` to the
store call. An owner's create/update activates immediately, exactly as today. A
non-owner's (newly reachable at all thanks to §2 — previously blocked by the blanket
`skill:edit` gate before ever reaching this logic) inserts inactive.

New route, `POST /agent-skills/{skill_key}/propose`, deliberately mirroring
`agent_profiles.py`'s `propose()` LINE FOR LINE wherever the shapes correspond —
including its exact `workspace_id` resolution (`str(target.scope_id) if
target.scope_id else await active_workspace_for_request(db, request)`, which for a
project-scope proposal passes the PROJECT's id as `workspace_id` alongside the
correct `project_id` — an existing Behavior imprecision, not something to "improve"
here; Skills' proposal flow should behave identically to Behavior's in every respect
that isn't specifically about which ORM row gets flipped, not diverge on a judgment
call about a value `create_request` treats loosely for queue-filing purposes only).

**Critical security property, verified against Behavior's actual code (not
assumed):** Behavior's `propose()` takes NO request body at all —
`propose(profile_id: str, request: Request, db) -> dict`. It derives `target_ref`,
`agent_id`, `scope`, `scope_id`/`project_id`, and `version` entirely from the row it
loads via `profile_id` (a server-trusted path parameter identifying one specific,
already-existing row), never from client input. Its own docstring explains why: *"A
client that could name it [target_ref] could point a proposal at any profile row in
the tenant and have the approval publish that instead."* Skills' `propose_skill`
MUST follow the identical principle — it must NOT accept a client-supplied
`target_ref` or `version`. Skills routes don't expose a raw row UUID in any URL
today (unlike Behavior's `/{profile_id}/...`), so `propose_skill` instead resolves
the target row itself, server-side, as "the newest INACTIVE version of this
`skill_key` at this `(scope, scope_id)`" — the row a preceding non-owner
`create`/`update` call (§5 above) just inserted with `activate=False`. If none
exists, 404 — there is nothing to propose.

```python
@agent_skills_router.post("/{skill_key}/propose", status_code=201)
async def propose_skill(skill_key: str, body: ProposeSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    if body.scope == "user":
        raise HTTPException(status_code=422, detail={
            "code": "NOT_A_SHARED_TIER",
            "message": "A personal default is yours alone; there is nobody to propose it to.",
        })
    perms = getattr(request.state, "permissions", []) or []
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, _user_id(request), perms, body.scope, body.scope_id,
    )
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Server-resolved, never client-supplied — mirrors Behavior's propose()
    # exactly: "the newest inactive version of this skill_key at this scope" is
    # the row a preceding non-owner create/update call just inserted.
    draft = await _store().get_latest_draft_version(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key,
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Nothing to propose")

    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415
    from shared.authz.effective_role import actor_display_name  # noqa: PLC0415
    from shared.authz.workspace import active_workspace_for_request  # noqa: PLC0415
    from shared.services import governance_requests as governance_service  # noqa: PLC0415
    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    scope_label = {"org": "organization", "workspace": "business unit", "project": "project"}[body.scope]
    request_type = f"agent_default_{body.scope}"
    async with get_db_session_for_tenant(tenant_id) as db:
        role = await effective_platform_role(db, request)
        name = await actor_display_name(db, request)
        workspace_id = body.scope_id if body.scope_id else await active_workspace_for_request(db, request)
        if not workspace_id:
            raise HTTPException(status_code=422, detail={
                "code": "NO_WORKSPACE",
                "message": "Choose a business unit before proposing an organization default.",
            })
        try:
            return await governance_service.create_request(
                db, tenant_id=tenant_id, initiator_id=_user_id(request), initiator_name=name,
                initiator_role=role, request_type=request_type,
                title=f"{body.agent_id} skill '{skill_key}' change ({scope_label})",
                description=f"{name} proposed a change to the '{skill_key}' skill for the {body.agent_id} agent ({scope_label} default), version {draft['version']}.",
                workspace_id=workspace_id, project_id=body.scope_id if body.scope == "project" else None,
                target_ref=str(draft["id"]), payload={
                    "agentId": body.agent_id, "skillKey": skill_key, "scope": body.scope,
                    "version": draft["version"],
                },
                system_raised=True,
            )
        except GovernanceError as exc:
            raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)})
```

(`ProposeSkillIn`: `agent_id: str`, `scope: str`, `scope_id: Optional[str]` only —
no `target_ref`, no `version`, no `workspace_id`. `get_latest_draft_version` is a
new `skill_store.py` function: queries `AgentSkill` for this `agent_id`/`scope`/
`scope_id`/`skill_key` where `is_active=False` and `deleted_at IS NULL`, ordered by
`version DESC`, `LIMIT 1`, returning `{"id": ..., "version": ...}` or `None`.)

### 6. `_apply_agent_default` gains a Skills fallback branch

`shared/governance/effects.py`'s `_apply_agent_default`: when the `AgentProfile`
lookup by `target_ref` finds nothing, try `AgentSkill` before raising
`EffectNotAvailable`:

```python
row = (await db.execute(select(AgentProfile).where(AgentProfile.id == target_uuid))).scalar_one_or_none()
if row is None:
    from shared.models.orm import AgentSkill  # noqa: PLC0415
    skill_row = (await db.execute(select(AgentSkill).where(AgentSkill.id == target_uuid))).scalar_one_or_none()
    if skill_row is None:
        raise EffectNotAvailable(request["type"], "That draft version no longer exists.")
    siblings = list((await db.execute(select(AgentSkill).where(
        AgentSkill.agent_id == skill_row.agent_id, AgentSkill.scope == skill_row.scope,
        AgentSkill.scope_id == skill_row.scope_id, AgentSkill.skill_key == skill_row.skill_key,
    ))).scalars().all())
    apply_publish_flip(siblings, skill_row.id)  # same pure function, works on AgentSkill rows unchanged
    await db.flush()
    from shared.services.skill_runtime import invalidate_skills_cache  # noqa: PLC0415
    invalidate_skills_cache(str(request["tenantId"]), skill_row.agent_id)
    return f"Published skill '{skill_row.skill_key}' v{skill_row.version} at {skill_row.scope} scope."
... existing AgentProfile path continues unchanged ...
```

No new governance request TYPE is introduced — Skills proposals reuse
`agent_default_org`/`_workspace`/`_project` exactly as Behavior does. This keeps the
entire routing/approver/self-approval/audit machinery, the frontend's approval queue
UI, and the existing `GOVERNANCE_APPROVER_ROLE`/`SYSTEM_RAISED`/`REQUEST_TYPE_LABEL`
catalogs completely untouched — a Skills proposal and a Behavior proposal both show up
as "Agent default — project" (etc.) in the same queue, distinguished by their own
`title`/`description` text, which already differ. Considered and rejected: a
`skill_default_*` type family, which would need parallel entries in 5+ shared
registries (backend `routing.py`, frontend `governance.ts`/`routing.ts`, the Zod
`GovernanceApprovalType` enum) for zero behavioral gain — the approver role, self-
approval rule, and system-raised handling are IDENTICAL for both resource kinds at
every tier; only the underlying row being flipped differs, and that's a
`target_ref`-driven runtime lookup, not a type-level distinction.

## Role journeys (this piece only)

- **Project Admin**: can now draft AND publish their own project's Behavior and
  Skills defaults directly — the tier they've always nominally owned, functional for
  the first time. Can also draft-and-propose to their project's parent workspace tier
  (one rung up), for either system.
- **BU Admin**: can now draft (not just publish) their own workspace's tier directly.
  Can draft-and-propose to org (one rung up).
- **Developer / BA / Architect / QA / Security Engineer / DevOps Engineer / Data
  Engineer / Scrum Master**: can now draft-and-propose to their PROJECT's tier (one
  rung up from their own "personal" position) for real, membership-scoped access —
  not a blanket tenant-wide permission anymore. A developer on Project A genuinely
  cannot touch Project B's tier, for either Behavior or Skills.
- **Contributor**: still cannot draft/propose at any shared tier (matches their
  documented "not enough to open an agent" floor) — can still use their personal
  tier (sub-project 2), unaffected here.
- **Org Admin**: unaffected — wildcard already covered every case.

## Out of scope for this sub-project

- Evaluation gating (golden tests, PASS-required-before-publish) — sub-project 4.
- Import/supply-chain screening — sub-project 5.
- Audit completeness — sub-project 6.
- The read-side visibility gap for org/workspace/project (anyone in the tenant can
  READ any tier's content today, regardless of membership) — this is intentional and
  pre-existing: those tiers are deliberately SHARED, unlike the personal tier sub-
  project 2 closed a genuine read leak for. Not touched here.
- A per-person (rather than per-role) override grant mechanism — noted as a possible
  future need in earlier research, not part of this sub-project.

## Testing

- Backend, pure-function: `resolve_actor_tier_access` table-driven across the full
  scope × role-binding matrix per the design above — including the negative cases
  (a bu_admin bound to Workspace X gets `(False, False)` for Workspace Y; a developer
  bound to Project A gets `(False, False)` for Project B's `may_propose`; contributor
  bound to a project gets `may_propose=False`).
- Backend, live-DB route-level: for BOTH Behavior and Skills — `project_admin` can now
  draft+publish their own project; `bu_admin` can now draft+publish their own
  workspace; a delivery role (e.g. `qa`) bound to a specific project can draft+propose
  to that project but 403s against an unrelated one; the resulting `GovernanceApproval`
  routes to the correct approver and is decidable by them; deciding it actually
  publishes (flips `is_active`) the right row for both `AgentProfile` and `AgentSkill`;
  self-approval is blocked identically for a Skills proposal as for a Behavior one
  (proving the reused governance type genuinely inherits that generic rule with zero
  new code).
- Frontend: no code changes are anticipated for Behavior (its propose UI already
  exists and is generic over scope). Skills gets a new "Propose" action mirroring the
  existing "Override" action's placement, shown when `!canManage && canPropose` — a
  smoke test confirming it renders and calls the new API client function with the
  right payload shape, following the same mocking convention as sub-project 1/2's
  Skills tests.
