# Development Agent — Verification & RBAC Completion — Design

Date: 2026-08-31
Owner: Sarthak
Scope: take the Development Agent (Portfolio 1, agent #3, `agent_id="development"`) through
the same "properly rebuild and verify" pass already completed for Security, Documentation,
and Code Review (`help/portfolio-1-agent-status.md`), so it can be the fourth agent flipped
live via `builtAgents`. This is **not** a rewrite — real, substantial code and a real,
matching frontend UI already exist (see Part 0). The work is: close a genuine RBAC gap,
prove the existing tool implementation actually works end-to-end, fix two real gaps found
during that proof, and verify prior-stage context actually reaches this agent when opened
from its standalone project page.

Related reading: `help/multi-track-agent-access-design.md` (the platform-wide access model
this agent must conform to), `help/portfolio-1-agent-status.md` (what "done" already means
for the three sibling agents, and Development's own access-hardening status), PRD §21.3 /
§22.3 (Development Agent's authoritative capability spec) and §20.2–20.4 (rules common to
every agent).

---

## Part 0 — What already exists (verified by direct comparison, not assumed)

Before scoping any work, the frontend and backend for Development were diffed byte-for-byte
against the last known-good build of this feature
(`C:\Users\srk02\Downloads\sdlc_product\platform`, the "old project" location). Result:
**nothing needs to be ported over.** The current repo already has everything that build had,
and in one respect (model resolution) is materially ahead of it.

- **Frontend** (`frontend/app/(app)/projects/[id]/development/page.tsx` and its four
  supporting files — `repo-picker-dialog.tsx`, `repo-file-tree.tsx`, `code-viewer.tsx`,
  `lib/api/dev-workspace.ts`) — identical to the old build, save two trivial fixes already
  applied here. A real "Pull repos" flow, a VSCode-style file-tree + Monaco viewer with
  changed-line highlighting, a PR list, and a chat drawer wired to
  `useAgentChat({ agent: "development" })` that refreshes the workspace after every agent
  turn. This is the UI the rest of this document builds on top of — not something to
  replace.
- **Backend** (`backend/agents_orchestrator/development_agent/`) — ~5,150 lines: a
  LangGraph graph (`agents/dev_agent.py`), and tools for git (`tools/git_tools.py`, 1,257
  lines — ADO and GitHub, clone/branch/PR/work-item), file read/write/search
  (`tools/file_tools.py`), scaffolding/generation (`tools/generation_tools.py`), sandboxed
  execution (`tools/code_execution_tools.py`, `tools/sandbox_policy.py`), linting
  (`tools/lint_tools.py`), and artifact handoff (`tools/artifact_tools.py`). Diffed against
  the old build's equivalent file: 14 lines of diff in `agents/dev_agent.py`, trivial. The
  old, separate `backend/agents/development_agent/` (non-`_orchestrator`, no `tools/`
  directory) is an earlier, abandoned precursor — already superseded, nothing to mine there.
- **Materially ahead of the old build**: `agent_node` (`agents/dev_agent.py:215-244`)
  resolves its model via `resolve_model_for_run` + `guarded_completion`
  (`shared/services/model_resolver.py`, `shared/services/model_call_wrapper.py`) — the real
  Model Gateway / BYOK integration. `help/portfolio-1-agent-status.md` documents that
  Security, Documentation, and Code Review were all built against a `resolve_chat_model`
  function that **did not exist** at the time (silent fallback to a dead `.env` key for
  every one of them). That gap has since been closed platform-wide (`model_resolver.py:263`)
  and Development's code already uses the fixed path — the other three agents' write-ups
  should be treated as describing a since-repaired limitation, not Development's own state.
- **RBAC data model already matches the target exactly**:
  `AGENT_DEFAULT_REACH["development"]` (`backend/config/agent_registry.py:243-247`) —
  `project_admin`/`architect` = owner, `developer` = build, `ba`/`qa`/`security_engineer` =
  use, `devops_engineer` = requests, `data_engineer` = none. This is precisely PRD §21.3's
  "Architect approves; Developer builds" plus the Appendix ownership matrix, already encoded
  as data, not something this document needs to design.
- **The project page's tile grid already gates on verification, not on code existing**:
  `builtAgents: Phase[] = ["security", "documentation"]`
  (`frontend/app/(app)/projects/[id]/page.tsx:122`) — Development renders `coming_soon`
  today specifically because nobody has completed this pass yet. Adding `"development"` to
  this array is the sole rollout step (Part 5) once everything below is verified.

## Part 1 — Scope & sequence

Because the architecture, RBAC data model, and UI already exist and match the target, this
is one arc, not several independent pieces of work:

1. Close the RBAC gap in `dev_workspace.py` and the `require_agent_access` helper it will use
   (Part 2).
2. Prove the existing ~5,150-line implementation actually works, tool by tool, against PRD
   §21.3's capability table — and close two real gaps found while doing so (Part 3).
3. Verify prior-stage artifact context actually reaches Development when opened from its
   standalone page, and fix the resolution strategy if (as suspected) it doesn't (Part 4).
4. Test everything above and flip `builtAgents` (Part 5).

## Part 2 — RBAC closure

### 2.1 A pre-existing gap in the shared `require_agent_access` dependency

`require_agent_access(agent_id)` (`shared/authz/agent_access.py:168-207`) is the router-level
dependency the design doc recommends, and it already gates `security_workspace_router`. But
it resolves the caller's role via `effective_platform_role` → `platform_role_for`
(`shared/authz/effective_role.py:95-101`), which — per that function's own docstring —
resolves a role the caller holds **anywhere in the tenant**, not scoped to the project in the
URL. It never calls `visible_project_ids` the way the chat-route helper
(`assert_agent_access_for_chat`, same file, lines 124-165) deliberately does. Concretely: a
Developer who is a member of Project A only, reaching a route like
`GET /dev/{projectB_id}/workspace/tree`, would be let through today, because
`AGENT_DEFAULT_REACH["development"]["developer"] == "build"` and nothing checks whether they
are actually a member of Project B.

This is the same class of bug the "bonus fix" note in
`help/portfolio-1-agent-status.md` (Part, "Shared foundation — done") describes fixing for
the chat routes specifically — `require_agent_access` was never given the equivalent fix, and
neither was anything that already depends on it (`security_workspace_router`).

**Fix**: add the same `visible_project_ids` membership check to `require_agent_access`'s
`_dep` (`shared/authz/agent_access.py:179-205`), mirroring
`assert_agent_access_for_chat:151-158`. This is a single, shared fix — it closes the leak for
Development and silently repairs Security's already-shipped workspace router at the same
time, with no changes needed to that router's own code.

### 2.2 Gate `dev_workspace_router`

`dev_workspace_router` (`shared/routers/dev_workspace.py:39`) is currently gated only by
`Depends(require_project_access())` — any member of the project, any role, can pull a repo
and browse its files and PRs through this page today; there is no per-agent check on any of
its six routes (`ado/projects`, `ado/.../repos`, `ado/.../branches`, `workspace/pull`,
`workspace` (GET), `workspace/tree`, `workspace/file`, `workspace/changes`,
`workspace/file/changed-lines`, `prs`).

**Fix**: add `Depends(require_agent_access("development"))` alongside the existing
`require_project_access()` dependency at router level (line 39), covering every route at
once, matching the pattern the design doc's §4.3 prescribes and Security's own workspace
router already uses (once 2.1's fix lands under it).

### 2.3 Frontend page-level gate

`frontend/app/(app)/projects/[id]/development/page.tsx` has no page-level access check today
— only the "Run Dev agent" button is wrapped in `RequireRole capability="run:trigger"`
(line 212), which is the coarse, non-agent-aware capability system the design doc's §4.4
flags as a source of drift from the real per-agent state. A viewer whose role has no
Development access can currently still browse the repo and read PRs on this page even though
the project page's own tile grid would show it `locked`.

**Fix**: gate the page itself using `tileStateFor` (`frontend/lib/agent-access.ts:85`) — the
same function the project page's tile grid already calls — rather than inventing a second
check. A `locked` or `coming_soon` result renders the existing `EmptyState`/`RequestAccessButton`
pattern already used elsewhere on this page, instead of the file browser.

### 2.4 Tests

- A cross-project-denial case, an `org_admin`-denial case, and an owning-role-success case
  against `dev_workspace_router`'s routes — mirroring
  `backend/tests/test_security_agent_chat_access.py`'s pattern, which today only covers the
  chat route, not the workspace router.
- A regression test proving `require_agent_access`'s fixed project-membership check actually
  closes the leak described in 2.1 (a role held only on a different project must be denied).

## Part 3 — Real-logic verification plan

### 3.1 Capability-to-tool mapping (PRD §21.3)

| PRD capability (Safe tier) | Tool(s) |
|---|---|
| Read/clone the repo | `clone_repo`, `get_ado_context`/`list_ado_projects`/`list_ado_repos`/`list_ado_branches`, `get_github_context`/`list_github_repos`/`list_github_branches` (`tools/git_tools.py`) |
| Read work items | `get_work_item`, `list_work_items` (`tools/work_item_tools.py`) |
| Scaffold/write/edit/search code | `generate_project_scaffold`, `generate_component`, `generate_api_endpoint`, `generate_database_migration` (`tools/generation_tools.py`); `read_file`, `write_file`, `edit_file`, `delete_file`, `list_directory`, `search_codebase` (`tools/file_tools.py`); `init_project_structure` (`tools/git_tools.py`) |
| Build/lint/test, sandboxed execution | `lint_and_validate_code` (`tools/lint_tools.py`); `execute_code_in_sandbox` (`tools/code_execution_tools.py`); `run_command` (`tools/git_tools.py`, policed by `tools/sandbox_policy.py::validate_command`) |
| Local branch + commit (not yet pushed) | `create_feature_branch`, `git_commit` (`tools/git_tools.py`) |

| PRD capability (Consequential tier, Architect approves) | Tool(s) | Gate status found |
|---|---|---|
| Push a branch; open/mark-ready a PR | `push_branch`, `create_pr`, `mark_pr_ready`, `create_github_pr_tool` (`tools/git_tools.py`) | **Already code-gated.** `push_branch` (line 868) and `create_pr` (line 927) check `s.push_gate_enabled`/`s.push_approved`, set from an explicit approval-phrase check on the user's turn (`development_agent_api.py:320-371`, `_is_push_approval`). Real HITL enforcement, not prompt-only — better than what Security/Documentation/Code Review had at their own verification time. |
| Create a repository; update work-item state; comment on work items | `create_ado_repo` (line 374), `update_work_item_state` (line 1204), `add_pr_comment_to_work_items` (line 1236) | **Ungated.** All three execute immediately on any model tool call — the same "prompt-text-only" pattern already documented as a known limitation on Code Review/Documentation. Per the 2026-08-31 conversation, **closing this is in scope**, not deferred. |

| PRD capability (Sign-off, Architect, formal) | Tool(s) |
|---|---|
| Accept the implementation (finalize) | `submit_development_artifacts` (`tools/artifact_tools.py:41`) |

### 3.2 Fix: gate the three ungated Consequential tools

Extend the existing `push_gate_enabled`/`push_approved` session-state mechanism
(`config/session_state.py:36-43`) to cover `create_ado_repo`, `update_work_item_state`, and
`add_pr_comment_to_work_items`, mirroring the exact check already proven correct for
`push_branch`/`create_pr` (`tools/git_tools.py:868,927`). Do not invent a second mechanism —
reuse the same flag and the same `_is_push_approval` phrase-detection
(`development_agent_api.py:332-339`), since PRD §21.3 groups all five of these tools under
one Consequential tier with one approver (Architect); there is no PRD basis for a second,
separate approval gesture.

### 3.3 Verification method

- A live end-to-end test driving the real compiled `dev_agent` graph against a real temp git
  repository (not a mock), scripting a realistic tool-call sequence: clone → read/edit files
  → lint → local commit → attempt push **without** approval (must be refused) → push **with**
  approval (must succeed) → create PR → attempt `create_ado_repo` without approval (must be
  refused, after 3.2's fix) → `submit_development_artifacts`. Mirrors the pattern already
  established in `test_security_agent_live_e2e.py` / `test_documentation_agent_live_e2e.py`.
- Direct spot-checks: `path_guard.py::resolve_safe_path` actually blocks a `../../../.env`
  traversal escape (same check already done for Code Review/Security's `read_repo_file`);
  `sandbox_policy.py::validate_command` actually refuses a disallowed command.
- With the real Azure model key (Part 5.1), at least one genuine, unscripted chat turn against
  a real test repo — proving the model's own judgment, which no sibling agent's verification
  pass could do without a working key.

## Part 4 — Upstream artifact context

### 4.1 What's confirmed correct

- `read_design_artifact()` (`tools/artifact_tools.py:18-37`) fetches **both**
  `requirements_payload` and `design_artifacts` despite its name — correct behavior, not a
  gap.
- `submit_development_artifacts()` (`tools/artifact_tools.py:41-118`) persists correctly via
  `patch_session_artifacts` and builds a proper handoff payload (`context_keys` naming all
  three upstream fields) for Testing to consume next.
- The `tenant_id=None` pattern in both calls is deliberate: `AgentSession`
  (`shared/models/orm.py:768-773`) is documented as intentionally global/non-RLS, keyed by a
  high-entropy `session_id`, matching legacy Django behavior. Not a tenant-isolation bug.

### 4.2 A header-text mismatch (minor, worth normalizing)

The system prompt (`prompts/dev_agent_prompt.py:38-46`) tells the model context is
pre-injected under literal `"Requirements Context"` / `"Design Context"` headers, and
instructs it **not** to call `read_design_artifact()` unless the user explicitly asks. Those
exact header strings are only ever produced by `orchestrator_api.py`
(lines 1396, 1413-1414, 1441-1442, 1457) — the Copilot pipeline path. The standalone page's
own context builder, `config/context_broker.py::build_context` (line 246), produces
differently-labeled `[REQUIREMENTS ARTIFACTS]` / `[DESIGN ARTIFACTS]` sections
(`_fmt_requirements`, `_fmt_design`, lines 19, 53). Likely harmless to the model in practice
(the content is still present in the message either way), but the two paths have silently
diverged from what the prompt assumes.

**Fix**: normalize `context_broker.py`'s section headers to match what the prompt actually
tells the model to expect (`"Requirements Context"` / `"Design Context"`), rather than
maintaining two label conventions for the same content.

### 4.3 The real gap: session continuity across stages

Two parallel artifact-storage models coexist:

- **`AgentSession`** (`shared/models/orm.py:768`) — keyed by `session_id`, what Development's
  own tools (`fetch_session_artifacts`/`patch_session_artifacts`) read and write.
- **`Run`** (`shared/models/orm.py`, used via `Run.development_artifacts` etc.) — keyed by
  `project_id` (+ run), what the Copilot pipeline and the page's own PR-list query
  (`dev_workspace.py:216-227`) use.

When the standalone Development page opens a fresh chat, `useAgentChat`'s `ensureSession`
(`frontend/hooks/use-agent-chat.ts:166-179`) calls `createConversation({ agentId, projectId,
title })`, which mints a **new, random session id** for that conversation thread. The
Development chat route defaults `session_id` to a fresh `uuid4()` when none is supplied
(`development_agent_api.py:284,343`). Nothing observed so far links this fresh id back to
whatever `session_id` the Requirements or Design agent used for *their* conversations — so
`fetch_session_artifacts(session_id)` on a brand-new Development conversation is very likely
to find nothing from prior stages, even on a project where Requirements and Design have both
been baselined.

A partial bridge exists in the other direction: `_persist_pr_to_run`
(`development_agent_api.py:793-829`) manufactures a fresh `Run` row carrying
`development_artifacts` so a created PR shows up in the page's own PR tab — but this is
PR-specific and does not copy `requirements_payload`/`design_artifacts` onto that row, and it
does not help Development *find* upstream context in the first place.

**Fix (per the 2026-08-31 conversation)**: resolve upstream context **by project, not by
session** — Development's context lookup should query the project's most recent `Run` row
(`requirements_payload`/`design_artifacts` columns) rather than depending on session-id
continuity across independently-created conversation threads. `Run`, not `AgentSession`, is
the correct canonical source: `help/portfolio-1-agent-status.md`'s Documentation section
documents `read_upstream_artifacts` already reading a real seeded `Run` row for exactly this
purpose (organization/workspace/project/run, with `requirements_payload` populated) — this
spec follows that established precedent rather than introducing a second convention.
`AgentSession` remains what Development's own tools use for *its own* session-local working
state (`dev_artifacts`, chat continuity) — only the upstream *read* moves to `Run`.

### 4.4 Verification

A live test: seed a real project with baselined Requirements + Design artifacts, open
Development fresh (new conversation, new session id, exactly as a real user would), and
confirm the project-scoped lookup surfaces them — not just that the JSON round-trips through
`read_design_artifact` in isolation.

## Part 5 — Testing & rollout

### 5.1 Environment setup

- A real Track 1 test project (currently zero projects/connectors exist in this dev
  database).
- A real Azure DevOps connector wired to that project's business unit (`workspace_connectors`
  is currently empty).
- The user's Azure model key added via the in-app Model Providers UI (BYOK), so
  `resolve_model_for_run` resolves a real model for `agent_type="development"`.

### 5.2 Full test list

1. RBAC: cross-project denial, `org_admin` denial, owning-role success — against
   `dev_workspace_router` (new coverage per 2.4).
2. Regression test: the `require_agent_access` project-membership fix (2.1) actually closes
   the leak.
3. Live end-to-end tool proof per 3.3, including the 3 newly-gated Consequential tools (3.2)
   refusing without approval and succeeding with it.
4. `path_guard` traversal-escape blocked; `sandbox_policy` disallowed-command blocked.
5. Upstream context: project-scoped lookup (4.3/4.4) actually surfaces a real project's
   Requirements + Design artifacts from a fresh standalone-page conversation.
6. At least one genuine, unscripted live model turn against a real test repo with the real
   Azure key.
7. Load/concurrency tests per 5.3: shared-workspace contention, sandboxed-execution
   contention, Model Gateway cap/rate-limit behavior under concurrent chat turns.

### 5.3 Load & concurrency testing

Added per the 2026-08-31 conversation — "enterprise project" scope, not covered by the
single-session live_e2e tests above. This agent's failure modes under load are structural,
not generic (a slow endpoint), given three real shared-resource constraints found while
reading the code:

- **Filesystem workspace isolation.** Every pulled repo lands at
  `ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "repo"`
  (`shared/routers/dev_workspace.py:82-84`) — one working directory per project, not per
  session/user. Two people opening the same project's Development page and pulling/editing
  concurrently share one checkout on disk. Load test: N concurrent sessions against the same
  project, confirming either serialized-safe behavior or an explicit conflict signal — not
  silent corruption of one user's in-flight edit by another's.
- **Sandboxed command execution.** `execute_code_in_sandbox`/`run_command`
  (`tools/code_execution_tools.py`, `tools/git_tools.py:769`), policed by
  `sandbox_policy.py`'s allowlist/timeout/output-cap. Load test: many concurrent sandbox
  executions across different sessions, confirming per-session timeouts and output caps hold
  under contention (no cross-session resource starvation) and that `ENABLE_WORKER_POOL`
  (`backend/.env`) — currently `false` in this dev environment — is a real, understood
  capacity knob before rollout, not an unexamined default.
- **Model Gateway cost/rate limits.** `guarded_completion` (`shared/services/model_call_wrapper.py`)
  enforces a per-call cost cap. Load test: concurrent chat turns across multiple
  sessions/projects against the same BYOK provider, confirming the cap and any provider-side
  rate limit degrade as a legible error to the chat UI (matching the PRD's "legible failure"
  state model, §37) rather than an unhandled exception or a silent hang.

Tooling: checked for an existing load-testing pattern (`*load*` under `frontend/`, `*locust*`/
`*load_test*` under `backend/`) — none exists yet. Build a minimal async harness (Python,
`asyncio` + the same WS client shape the WS chat tests already use) driving N concurrent
sessions against a real test project, scoped to what these three constraints actually need
proven, not a generic throughput benchmark or a new framework dependency.

### 5.4 Rollout

Once every test above passes: add `"development"` to `builtAgents`
(`frontend/app/(app)/projects/[id]/page.tsx:122`) — the same one-line flag that made
Security and Documentation live. No other frontend change is required for the tile to render
correctly under its track with the right lock states, per the existing comment at that line.

---

## Open items carried forward (not blocking this pass)

- `create_ado_repo`/`update_work_item_state`/`add_pr_comment_to_work_items`'s newly-added gate
  (3.2) reuses the single existing `push_approved` flag; if a future requirement needs
  per-action approval granularity (e.g. approving a PR push without also pre-approving a
  work-item comment on the same turn), that would need its own design — out of scope here,
  no such requirement exists in PRD §21.3 today.
