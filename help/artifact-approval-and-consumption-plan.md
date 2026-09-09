# Agent ownership, artifact publication, and cross-agent consumption

## Context

Agents hand work to each other — Design reads Requirements, Code Review reads Design,
Deployment reads Testing and Security. Two things are missing, and the second depends on
the first:

1. **Ownership is not single-sourced.** Three maps claim to say who owns each agent, they
   disagree, and a `.get(..., "project_admin")` default hides the disagreement.
2. **No consumer checks approval.** Any agent reads any other agent's latest output
   whether or not a human ever accepted it.

You cannot build "the consumer requests approval from the agent's owner" on top of an
owner map that is wrong for two of the nine stages. Ownership is fixed first.

Everything below was verified by execution against this repo on 2026-09-04, not read off
the architecture.

---

## Part 1 — Ownership, as it actually stands

### Three maps, three key sets

| Map | Keys | Notes |
|---|---|---|
| `frontend/lib/roles.ts::AGENT_OWNER_ROLE` | **14** phases | The complete one. Uses UI name `review`; has `plan: scrum_master` |
| `backend/shared/governance/routing.py::AGENT_OWNER_ROLE` | **13** | **Missing `plan` and `code_review`.** Keyed on UI name `review` |
| `backend/shared/authz/permissions.py::_PHASE_PERMISSION` | **9** stages | stage → `artifact:approve_<stage>`. Uses backend name `code_review` |

`agent_owner_role()` (`routing.py:378`) is `AGENT_OWNER_ROLE.get(phase, "project_admin")`.
The default means a missing key never raises — it silently answers "Project Admin".

### Verified defect — the named owner cannot approve

Computed against the live maps:

```
stage           agent_owner_role()   holders of artifact:approve_<stage>
code_review     project_admin        ['architect']    <-- OWNER CANNOT APPROVE
plan            project_admin        ['project_admin', 'scrum_master']
```

**`code_review` is the live bug.** `shared/authz/consequential.py:82` calls
`agent_owner_role(stage)` with the *backend* stage name `code_review`. The map only holds
the *UI* name `review`. So the platform tells a user to get sign-off from a Project
Admin, and a Project Admin who agrees cannot give it — `artifact:approve_code_review` is
held by `architect` alone. The advice and the enforcement point at different people.

**`plan` is the same fault, currently masked.** It resolves to `project_admin`, who does
happen to hold `artifact:approve_plan`, so nothing visibly breaks — but the frontend says
the owner is `scrum_master`, and the moment `project_admin` loses that permission the
Plan gate loses its named owner too.

Both are one root cause: **a map keyed on UI names, called with backend stage names, with
a default that swallows the miss.**

### The five track agents are not backend agents at all

`STAGE_ORDER` derives from `AGENT_REGISTRY`, which has exactly the nine pipeline agents.
`discovery`, `strategy`, `migration_mapping`, `validation`, `data_engineering` are **not
in it**, so a run can never sit at one of those stages.

So `_PHASE_PERMISSION`'s nine are *complete* for the run gate — no gap there. But
`frontend/lib/catalogue.ts` renders all fourteen as agents with owners, and
`routing.py::AGENT_OWNER_ROLE` routes agent-access requests for them. **Any
artifact-ownership feature must state whether those five are in scope**, or it will
inherit a fourteen-agent UI on top of a nine-agent backend.

### What already exists and should be extended, not rebuilt

`agent_access` is *already* the two-stage request this feature needs:

- stage one is always `project_admin` — the cheaper question, "should this person be
  doing this work at all", and a no there saves the owner a decision
  (`governance_requests.py:384`)
- stage two is `agent_access_approver(stage, phase)` → the agent's owner
- `next_agent_access_stage` collapses to a single approver where the Project Admin *is*
  the owner, so nobody is asked to approve their own decision

| Existing | Use it for |
|---|---|
| `shared/services/approval_requests.py` — polymorphic `subject_kind`/`subject_id`, `target_role`, `request_type`, `SelfApprovalBlocked`, `ApprovalAlreadyDecided` | the consumption request |
| `agent_access` two-stage routing in `governance_requests.py` | the exact PA→owner shape, already proven |
| `_PHASE_PERMISSION` + `has_permission` | the publication gate |
| `shared/routers/approvals.py` | surfacing requests in the queue that already exists |
| `shared/services/deployment_gate.py` | freeze-at-approval + audit carrying the frozen subject |
| `frontend/lib/catalogue.ts::ownerRole`, `agentsOwnedBy()` | the "who owns what" view |

---

## Part 2 — The artifacts, and why one of them cannot be approved today

**Blob artifacts** — `artifacts` rows. DOCX, PDF, diagrams. They already carry
`approval_status` / `approved_by` / `approved_at`, bytes held under a `_pending` prefix
until approval moves them. Approved via `POST /artifacts/{id}/approve`.

**Stage payloads** — JSONB on `runs.{stage}_artifacts` and
`agent_sessions.{stage}_artifacts`. **This is the actual handoff between agents, and it
has no approval state at all.**

Two consequences:

- **No consumer gates.** `read_upstream_artifacts` (deployment, documentation),
  `read_design_artifacts` (code review), and security's direct
  `select(Run.design_artifacts)` all take the latest non-null payload ordered by
  `created_at desc`. A grep for `approval_status` across `agents_orchestrator/` hits one
  file — the deployment gate.
- **The payload is mutable, so approving it would be theatre.**
  `patch_session_artifacts(...)` writes the column in place; no version history. Approve
  today's design and the next run replaces it — the approval record survives, the thing
  it approved does not. The Requirements page projects these as artifacts with
  `status: draft` and synthesised ids, which is why "approve a requirements artifact"
  found nothing to act on.

**Immutability is the precondition for every other phase here.**

---

## Decisions taken

- **Phase 0 ships on its own, first.** It fixes a defect that exists today and is useful
  even if everything below is deferred. Nothing else starts until the owner map is
  single-sourced.
- **The five track agents are out of scope.** Ownership and publication cover the nine
  agents in `AGENT_REGISTRY`. The catalogue marks the other five as not-yet-built so the
  UI stops implying they own artifacts they cannot produce.
- **Publish once, consume freely.** The owning role signs a *version*; any stage in the
  project may then consume it, and every consumption is recorded. Per-consumer approval
  on the normal path is up to 72 pairs per project — rubber-stamped within a week,
  producing an audit trail that looks like scrutiny and records none.
- **One approver per publication.** The deployment *execution* gate already requires a
  second human downstream.
- **No auto-publish** — made moot by the self-publication rule anyway.
- **A version references the blob artifacts it covers**, so a design document and its
  diagram publish as one signed unit. `artifacts.approval_status` stays as it is.
- **Enforcement is a per-project flag, off by default.**

---

## Phases

### Phase 0 — one owner map, and prove its gate can be passed  ✅ DONE

Two failures this closes, both seen for real: an owner named to the user who cannot
approve (`code_review`, above), and `artifact:approve_deployment` held by nobody because
no user had `devops_engineer` — correct code that read as broken.

- Make `routing.py::AGENT_OWNER_ROLE` complete and keyed on **backend stage names**, with
  the UI names as explicit aliases. Add `plan` and `code_review`.
- **Delete the `"project_admin"` default** from `agent_owner_role()`. An unknown phase
  must raise, not answer plausibly.
- Startup assertion, beside `assert_rbac_catalog` in `shared/authz/catalog.py`: for every
  stage in `STAGE_ORDER`, its owner role holds `_PHASE_PERMISSION[stage]`, and at least
  one active user in the tenant holds that role. Fail loudly.
- A test pinning the three maps against each other, so they cannot drift apart again —
  this is the actual root cause, not any single wrong value.

Files: `backend/shared/governance/routing.py`, `backend/shared/authz/catalog.py`,
`backend/tests/test_agent_ownership_is_single_sourced.py` (new).

Track agents are out of scope (see Decisions). Concretely: `routing.py::AGENT_OWNER_ROLE`
keeps its entries for the five so existing agent-access routing is unchanged, but the
startup assertion iterates `STAGE_ORDER` — the nine — and `catalogue.ts` marks the five
as not-yet-built rather than showing them as artifact owners.

### Phase 1 — versions (immutability)  ✅ DONE

New table `artifact_versions`, FORCE RLS on `app.current_tenant_id` (**not**
`app.tenant_id` — that name matches nothing and reads as permanently empty):

```
id, tenant_id, project_id, stage, run_id,
version int,                  -- unique (project_id, stage, version)
payload jsonb,                -- FROZEN at creation, never updated
content_hash varchar(64),     -- sha256 of the canonical payload
covers jsonb,                 -- blob artifact ids this version signs off
status varchar(16),           -- draft | published | rejected | superseded
produced_by, published_by, published_at, rejection_reason, created_at

check (status <> 'published' or published_by is not null)
```

`runs.{stage}_artifacts` stays the agent's working draft; publishing copies it into a
frozen version. A new run creates N+1 and never edits N.

Files: migration `0044_artifact_versions.py` (head is `0043_deployments`),
`backend/shared/models/orm.py`, `backend/shared/services/artifact_versions.py` (new).

### Phase 2 — the publication gate  ✅ DONE

`POST /projects/{project_id}/stages/{stage}/versions/{version}/publish` and `/reject`.

- `require_permission(_PHASE_PERMISSION[stage])` plus project scope — chosen consciously
  per route (see RBAC traps)
- **self-publication refused**: `published_by` may not equal `produced_by`, matching the
  run gate (`tests/test_gate_self_approval.py`) and the deployment gate
- `audit_events` row carrying `content_hash` and the payload, so the evidence of what was
  signed does not depend on the row staying unedited
- the previous published version becomes `superseded`, never deleted

Frontend: a Publish action on the stage page; the artifact list shows
`draft / published / superseded` instead of today's flat `draft`.

Files: `backend/shared/routers/artifact_versions.py` (new, mounted in `process_api.py`),
`frontend/app/(app)/projects/[id]/{requirements,design,plan}/page.tsx`.

### Phase 3 — consumption through one helper  ✅ DONE

Replace the four hand-rolled readers with:

```python
async def read_upstream(project_id, stage, *, consumer_stage, consumer_id) -> UpstreamRead
```

Returns the latest **published** version, records an `artifact_consumptions` row
(version, consuming stage, run, who, when), and returns `None` with a reason when nothing
is published.

**There is no fallback to the draft.** "No approved design exists yet" is the answer. A
fallback would make the gate decorative, exactly as the tenant-wide credential fallback
made "Needs a credential" decorative until it was removed.

Gated on `projects.enforce_artifact_publication`, default false.

Call sites: `deployment_agent/tools/deploy_tools.py:152`,
`documentation_agent/tools/doc_tools.py:191`,
`code_review_agent/tools/review_tools.py:183`,
`security_agent/tools/security_tools.py:232,261,297`.

### Phase 4 — the request, for the cases that are real  ✅ DONE

`request_type="artifact_consumption"`, `subject_kind="artifact_version"`,
`target_role=agent_owner_role(producing_stage)` — now trustworthy because of Phase 0.
Routed through the existing two-stage `agent_access` shape.

Only three cases raise a request; everything else is the free path:

| Case | Why it is a real question |
|---|---|
| Consuming an unpublished draft | The owner has not signed it; the consumer is asking them to vouch for unfinished work |
| Cross-project / cross-BU reuse | Another team's design — ownership and blast radius genuinely differ |
| Pinning a superseded version | Usually a mistake, occasionally deliberate |

### Phase 5 — visibility: who may read whose artifacts  ✅ DONE

The half of the ask that is not enforcement. A per-project matrix — consuming agent ×
producing agent — showing allowed / needs-request / denied, plus the owning role for each
producing agent and what is currently published.

Built from `catalogue.ts::ownerRole` and `agentsOwnedBy()` rather than a second
hardcoded table. Read-only; the request is raised from a cell.

### Phase 6 — supersession notices  ✅ DONE

A consumer pinned to version N is **notified** when N+1 publishes, not switched. Silent
upgrade is how a design change reaches production unreviewed, and it is
indistinguishable from correct behaviour until something breaks.

### Phase 7 — the evidence view  ✅ DONE

"What did this run build on" and "what consumed this version", from
`artifact_consumptions`. Free once Phase 3 records, and it is what an auditor asks for.

---

## RBAC traps this design must survive

- **Two checks, chosen consciously per route.** `require_permission` says the caller takes
  this kind of decision; project scope says which project. `artifacts.py` uses
  `assert_can_administer_project`; the deployment routes deliberately do not, because
  `devops_engineer` is not a project admin and requiring both would grant the permission
  to nobody who could use it.
- **`project_admin` is not a universal approver.** It appears in `_PHASE_PERMISSION` only
  where it owns the stage — and the `.get(..., "project_admin")` default in Phase 0 is
  precisely the bug of pretending otherwise.
- **Self-approval must hold even for the person holding the permission.** Verified live:
  the devops_engineer could not approve his own deployment request.
- **Connector access stays a separate lattice.** read / write / both per (stage,
  connector), verified working with an unrecognised mode denying everything. Publication
  governs artifacts; it must not be conflated with what a stage may do to Jira.
- **Credentials are per-user-per-project** (verified 14/14). `produced_by` on a version
  answers "whose credentials made this", which matters when the ADO or Jira account
  behind a run is not the platform user.

## Verification

**Phase 0 first, and independently** — it is the one phase that fixes a live defect:
assert `agent_owner_role("code_review") == "architect"` and that the returned role holds
the matching `artifact:approve_*` for every stage in `STAGE_ORDER`; assert an unknown
phase raises rather than returning `project_admin`.

**Per phase, backend:** unit tests beside the existing suites —
`tests/test_artifact_versions.py`, extending the patterns in `test_deployment_gate.py`.
Cover: a published version is immutable; self-publication refused; an unrecognised status
fails closed; `content_hash` changes when the payload does.

**Isolation, against the real database** — the shape of `scratchpad/isolation_matrix.py`:
another tenant sees nothing, an unset tenant reads empty, RLS keys off
`app.current_tenant_id`.

**End to end in the UI**, as bruno@abcbank.com on `test-demo`
(`92fbce7d-3fa0-4a2e-93c4-b865435b42f6`), which has 15 real Jira stories and working
per-user credentials:
1. Requirements produces a payload → publish v1 as the `ba` → confirm a second user
   cannot publish their own produced version
2. Turn on `enforce_artifact_publication` → Design reads v1 and records a consumption
3. Requirements re-runs → v2 draft; Design still sees v1 until v2 publishes
4. Publish v2 → Design is notified, not silently switched
5. `artifact_consumptions` answers what Design built on

**Do NOT run the full backend suite against the dev database.** It empties it — projects,
role bindings, credentials and `app_secrets` all went to zero this session, and the
model-provider keys and connector tokens are not recoverable. Run specific test files.

## Open

- Whether `documentation` keeps `project_admin` as its approver, or gains a dedicated
  role. It is the one stage whose approver is not a subject-matter owner.
