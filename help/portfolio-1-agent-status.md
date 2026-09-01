# Portfolio 1 (Greenfield/Brownfield) — Agent Build Status

**Purpose.** One shared, living reference for everyone building out the 8 Portfolio-1
agents (Track 1 — Greenfield, Track 2 — Enhancement & Support, which reuses the same 8).
Each agent has its own owner and its own section below — update your section as you
work. Full background: [`multi-track-agent-access-design.md`](./multi-track-agent-access-design.md)
in this same folder (Parts 1–3 for what each agent does and who owns it; Part 5 for the
full build/rebuild checklist this status table tracks against).

**Status as of 2026-08-20: the access-hardening pass (below) is complete for all 8
agents** — every WS handler and REST `/chat/` route now calls
`assert_agent_access_for_chat`, checked on every message, with no identity trusted
from client-supplied form fields. This is **not** a full agent rebuild — see "What
'done' means" below for exactly what this pass does and does not cover. Branch:
`portfolio1-access-hardening`.

**If your Claude session is picking this up cold:** read the design doc first, then find
your agent's section below, then read the relevant router file it names. The Security
section is the completed reference example — copy its pattern, don't reinvent it.

---

## Shared foundation — done (PR #12, PR #13)

- [x] `projects.track` column
- [x] `TRACK_PORTFOLIOS` / `AGENT_DEFAULT_REACH` (`backend/config/agent_registry.py`) — data model, PRD-verified
- [x] Dual-grain `agent_access_overrides` (role-wide OR one named person)
- [x] `require_agent_access(agent_id)` / `assert_agent_access(...)` (`backend/shared/authz/agent_access.py`) — the enforcement primitive every router below calls
- [x] Frontend tile states (owner/use/locked/coming soon), `frontend/lib/agent-access.ts`
- [x] `POST /runs`'s `active_agents` list validated against the caller's per-agent access (`backend/shared/routers/runs.py`) — reuses `assert_agent_access_for_chat`
- [x] **Bonus fix, found while wiring this up:** `assert_agent_access` alone doesn't check project *membership* — `platform_role_for` resolves a role the caller holds *anywhere in the tenant*, so a Developer on Project A could reach Project B's agents just by naming them. Added `assert_agent_access_for_chat()` (`backend/shared/authz/agent_access.py`) to close this — it's now the one call every agent's WS handler, REST `/chat/` route, and `POST /runs` all use. **If you're hardening one of the 7 agents below, call this, not `assert_agent_access` directly.**

## What "done" means for an agent below (access-hardening pass only — NOT a full rebuild)

This pass does **not** touch each agent's actual LangGraph logic (codegen, scans, test
generation, etc.) — that's Part 5 steps 3/6 of the design doc, a separate, later,
per-agent effort. This pass only closes the access-control gap described in the design
doc's §4.1: every Portfolio-1 router is currently reachable behind nothing but the
generic `artifact:view` floor, with no per-agent check and (on every REST `/chat/` route)
a client-supplied `user_id` form field that used to be trusted for identity. "Done" here
means, for one agent:

1. Its WS route (`/sdlc/agent/<agent>/ws`) resolves identity from the redeemed ticket
   (`claims.get("user_id")`) — already true everywhere; nothing to fix here.
2. Its WS message handler calls
   `assert_agent_access_for_chat(db, tenant_id=..., project_id=..., user_id=..., agent_id="<id>")`
   (from `shared.authz.agent_access`) on **every** message, not just the first (a
   session can be reused across projects client-side). **Use this helper, not
   `assert_agent_access` directly** — it also checks the caller is actually a member
   of the project, which `assert_agent_access` alone does not (see the "bonus fix"
   note above).
3. Its REST `POST .../chat/` route stops trusting the `user_id` Form field for identity —
   pulls `request.state.user_id` / `request.state.tenant_id` instead — and calls the
   same `assert_agent_access_for_chat` helper before doing any work. `user_id` stays
   in the form signature for wire compatibility but is documented as unused for auth
   (see Security's `chat()` for the exact comment to mirror).
4. A quick live/test check that an org_admin (holds `admin:*`, zero default agent access)
   gets denied, and the agent's owning role gets through — mirroring
   `backend/tests/test_security_agent_chat_access.py`. Also worth one cross-project
   case (role held on a different project must still be denied here) — see that same
   file's `test_a_role_held_only_on_a_different_project_does_not_reach_this_ones_security_agent`.

Reference implementation, already merged: `backend/agents_orchestrator/security_agent/security_agent_api.py`
(`_process_ws_message`, `chat()`) + `backend/tests/test_security_agent_chat_access.py`.

---

## Per-agent status

### 1. Requirements — owner role: BA — `agent_id="requirements"` — **access-hardening DONE**
File: `backend/agents_orchestrator/requirements_agent/requirements_agent_api.py`
- [x] WS handler (`_process_user_message_ws`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`/`tenant_id` Form fields, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_requirements_agent_chat_access.py` (3 passed)
- Notes: this route trusted the Form `tenant_id` pervasively (audit, langfuse, skills, session persistence), not just at the access check — all of those call sites were switched to the verified `request.state.tenant_id` too, not only the gate itself. Worth checking whether the same broader pattern exists in agents not yet touched.

### 2. Design — owner role: Architect — `agent_id="design"` — **access-hardening DONE**
File: `backend/agents_orchestrator/design_architecture_agent/design_architecture_agent_api.py`
- [x] WS handler (`_process_user_message_ws`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_design_agent_chat_access.py` (3 passed)
- Notes: **this route previously had no `project_id` Form field at all, and no tenant scoping** — a comment in the original code said "This REST endpoint carries no tenant_id (unlike the WS path)." Added `project_id: str = Form(...)` as a new required field to make the check possible — a real contract change, but safe: Design isn't in the frontend's `builtAgents` list yet, so nothing live depends on the old (broken) contract.

### 3. Development — owner role: Architect (Developer builds) — `agent_id="development"` — **access-hardening DONE; real-logic verification IN PROGRESS (2026-08-31)**
File: `backend/agents_orchestrator/development_agent/development_agent_api.py`
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_development_agent_chat_access.py` (3 passed)
- Notes: no `project_id` Form field here either — project id only exists inside the `pipeline_context` JSON field, reused via the existing `_lf_pid` extraction rather than adding a new field.

**Real-logic verification pass started 2026-08-31.** Full design:
`docs/superpowers/specs/2026-08-31-development-agent-verification-design.md`. Unlike Code
Review/Security/Documentation at their own verification time, this agent's model resolution
was found to already use the real, working `resolve_chat_model`/`resolve_model_for_run` path
(`agents/dev_agent.py:215-244`) — the dead-`.env`-key fallback documented below for the other
three agents does not apply to Development; that gap has since been closed platform-wide.

Frontend (`frontend/app/(app)/projects/[id]/development/page.tsx`) and backend
(`backend/agents_orchestrator/development_agent/`, ~5,150 lines) were diffed byte-for-byte
against the last known-good build and found already complete — a real "Pull repo" flow
(ADO + GitHub), a VSCode-style file-tree + Monaco viewer, a PR list, and a chat drawer with
real git/file/lint/sandbox/work-item tools. Nothing needed porting over. What this pass
actually found and is fixing:

1. **RBAC gap**: `require_agent_access()` (`shared/authz/agent_access.py:168`) resolves role
   via `platform_role_for`, which is **not** project-scoped — a role held on Project A reaches
   Project B's agent-gated routes. This affects `dev_workspace_router`
   (`shared/routers/dev_workspace.py` — pull/tree/file/changes/PRs, currently gated only by
   generic project membership, no per-agent check at all) and, as a pre-existing latent bug,
   the already-shipped `security_workspace_router` too. Fix is one shared change to
   `require_agent_access`, closing both.
2. **Two Consequential-tier tools ungated**: `create_ado_repo`, `update_work_item_state`,
   `add_pr_comment_to_work_items` (`tools/git_tools.py`) execute on any model tool call with
   no approval check — `push_branch`/`create_pr` already have a real code-level HITL gate
   (`push_gate_enabled`/`push_approved`, `tools/git_tools.py:868,927`); the fix extends that
   same mechanism to the other three rather than inventing a second one.
3. **Upstream context likely doesn't reach the standalone page**: opening Development fresh
   mints a new random `session_id` (`createConversation` → `development_agent_api.py:284,343`)
   unrelated to whatever session Requirements/Design used — `fetch_session_artifacts` on that
   fresh id is very unlikely to find prior-stage artifacts. Fix: resolve context by project
   (the project's most recent `Run` row, matching Documentation's already-established
   `read_upstream_artifacts` precedent) instead of by session continuity.

Once verified end-to-end (including load/concurrency testing — shared per-project workspace
directory, sandboxed-execution contention, Model Gateway cap under concurrent turns), the
sole rollout step is adding `"development"` to `builtAgents`
(`frontend/app/(app)/projects/[id]/page.tsx:122`) — same one-line flip as Security/Documentation.

### 4. Code Review — owner role: Architect — `agent_id="code_review"` — **access-hardening DONE; real-logic verification DONE (no builtAgents flip yet — see below)**
File: `backend/agents_orchestrator/code_review_agent/code_review_agent_api.py`
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_code_review_agent_chat_access.py` (3 passed)
- Notes: this route has no `project_id` Form field either — the review target is bound out-of-band by a separate `POST /review/prepare` call into an in-memory session; the REST check reads the session's already-bound `project_id` instead.

**Real-logic verification (2026-08-20), Part 5 steps 3/6, done without a live LLM key**
(no working provider is configured; the `.env` `ANTHROPIC_API_KEY` fallback is dead — 401).
Result: **this agent's code was NOT a stub** — contrary to the design doc's blanket
"assume broken" default, `agents_orchestrator/code_review_agent/` is a real, ~1,100-line
LangGraph implementation (graph, 7 tools, prompt, typed Pydantic artifact models),
already following the exact required pattern (§20.2). What was actually missing was
*proof* it works, not the code itself:
- The 9 pre-existing tests (`test_code_review_agent_graph.py` etc.) are import-smoke
  and isolated-unit tests only — none of them ever ran the actual agent loop.
- **New**: `backend/tests/test_code_review_agent_live_e2e.py` — drives the real compiled
  `reviewer.app` graph with only the model's `.ainvoke()` response scripted (3 canned
  turns); every tool call is the real implementation, including a genuine `semgrep`
  subprocess run against a fixture file with two real, known vulnerabilities (shell=True
  subprocess, MD5 password hash) generated into a fresh temp dir at test time. Proves the
  agent→tools→agent→END loop, real tool dispatch, real Semgrep execution, and real
  `submit_code_review` parsing/persistence all work together. **Not proven: the model's
  own judgment** (which findings it decides matter, review quality) — that needs a real
  LLM call, which needs a working provider key (see Open item below).
- `read_repo_file` / `search_repo` spot-checked directly (real file reads, real repo
  search, and the path-traversal guard actually blocks a `../../../.env` escape attempt).
- `run_semgrep_scan` verified with a real subprocess call finding real issues — **gotcha
  found**: Semgrep's `--config auto` silently skips any path it default-ignores
  (patterns including `test`/`fixtures` in the path name) *and* any file not tracked by
  git when run inside a git repo — a scan against an untracked/ignored-looking path
  returns `"status": "ok", "findings": []]`, not an error. Not a bug in our tool (a real
  cloned PR/branch always has its files committed), but worth knowing if you're
  hand-testing this agent.
- **Semgrep CLI is not declared as a project dependency** — `uv add semgrep` resolves a
  broken wheel on Windows (a `semgrep-core` binary with no `.exe` extension and missing
  DLLs; fails with `FileNotFoundError` on any real scan). Working install command:
  `uv pip install semgrep==1.173.0` (plain `uv pip install`, not `uv add` — the stricter
  `uv add` resolver also refuses this exact version over an unrelated `click` pin
  conflict). This is intentionally **not** added to `pyproject.toml`/`uv.lock` yet — do
  that properly (probably by relaxing the `click==8.2.1` exact pin project-wide, which
  needs its own check for what else depends on that exact pin) before this ships, rather
  than forcing a resolver workaround into the lockfile.
- **`POST /review/prepare` needs a live Azure DevOps connector** (clone URL + PAT) — the
  actual "click Select target, pull the repo" flow the frontend exposes cannot be
  exercised without one configured on a real project. Not tested end-to-end through the
  UI for this reason; the live_e2e test above seeds the equivalent session state directly
  instead, bypassing the ADO dependency.

**Open items before `builtAgents` can include `"code_review"`:**
1. A working LLM provider key (BYOK in-app, or a valid `.env` fallback) to prove the
   model's actual review judgment, not just the plumbing around it.
2. A real ADO (or other connector) end-to-end pass through the actual "Select target" UI
   button, once a connector is available on a test project.
3. Declaring `semgrep` properly in `pyproject.toml` (see gotcha above).

### 5. Security — owner role: Security Engineer — `agent_id="security"` — **DONE (reference implementation)**
File: `backend/agents_orchestrator/security_agent/security_agent_api.py`
- [x] WS handler: `assert_agent_access_for_chat` added, checked on every message
- [x] REST `/chat/`: `request.state.user_id` used, `user_id` Form field no longer trusted
- [x] Project-membership check added on both routes (404 if caller isn't on the project) —
      via `assert_agent_access_for_chat`'s `visible_project_ids` check
- [x] `security_workspace_router` gated with `Depends(require_agent_access("security"))`
- [x] Tests: `backend/tests/test_security_agent_chat_access.py` (incl. the cross-project
      leakage case), `backend/tests/test_security_workspace_agent_access.py`
- [x] Frontend `builtAgents` includes `"security"` — the one live, clickable tile today
- Landed in: PR #12, PR #13 (both merged/open against `main`)

**Real-logic verification (2026-08-20), Part 5 steps 3/6, done without a live LLM key**
— same technique and same result as Code Review below: **this agent's code was NOT a
stub either.** `agents_orchestrator/security_agent/` is a real ~1,280-line
implementation — the graph (`agents/scanner.py`), 4 real scanning tools (Trivy/SCA,
Semgrep/SAST, Gitleaks/secrets, a manifest-parsing SBOM builder), repo read/search,
design-artifact lookup, and `submit_security_review`.
- **New**: `backend/tests/test_security_agent_live_e2e.py` — drives the real compiled
  `scanner.app` graph with only the model's `.ainvoke()` scripted (4 turns — see the
  completion pass below for why `scan_dependencies` was split into its own turn). Every
  tool call is real: real Trivy scan finding a genuine CVE (flask 0.12.2 →
  CVE-2018-1000656), real Semgrep finding a real `shell=True` pattern, real Gitleaks
  finding a real hardcoded GitHub token, and the SBOM builder correctly parsing a real
  `requirements.txt`. `submit_security_review` builds and persists the artifact
  correctly. **Not proven: the model's own judgment** — needs a working LLM key (see
  Open items below).
- `read_repo_file` / `search_repo` spot-checked directly (same as Code Review — real
  reads, real search, path-traversal guard confirmed blocking a real escape attempt).
- **Neither Trivy nor Gitleaks were installed** in this dev environment (Semgrep was,
  from the Code Review pass). Installed both via `winget`:
  `winget install --id Gitleaks.Gitleaks` and `winget install --id AquaSecurity.Trivy`.
  **Gotcha**: winget updates the persistent Windows User `PATH`, but does **not** refresh
  it in already-running shells (including this session's) — new terminals opened after
  install pick it up fine; anything already running needs its `PATH` extended manually
  or to be restarted. Neither tool is declared as a project dependency yet (same
  situation as Semgrep — see the Code Review section above); for now this is a documented
  manual environment-setup step, not something `uv sync`/`npm install` gets you.

**Completion pass (2026-08-20)** — three real-logic gaps found during the verification
above were fixed, each with its own test (mirrors how Code Review's section above
documents its live-verification findings):
1. **Model resolution seam.** `agent_node` used to build `ChatAnthropic` inline, always
   hitting the raw `.env` `ANTHROPIC_API_KEY` and ignoring any in-app BYOK provider.
   Added `_resolve_model(state)` to `agents/scanner.py`, mirroring Code Review's pattern
   (try BYOK's `resolve_chat_model` first, fall back to raw `ChatAnthropic` if that
   raises) — closes the gap called out in the previous version of this note. Tests:
   `test_security_agent_tools.py::test_resolve_model_tries_byok_first_and_returns_it_on_success`,
   `::test_resolve_model_falls_back_to_raw_chat_anthropic_when_byok_raises`. The
   live_e2e test now mocks this the same way Code Review's does:
   `patch.object(scanner, "_resolve_model", return_value=<scripted model>)`, no longer
   patching `langchain_anthropic.ChatAnthropic` directly.
   **Caveat, found during this task's review (grep-verified, not a guess):
   `shared.services.model_resolver.resolve_chat_model` does not exist anywhere in the
   backend** — zero matches for `def resolve_chat_model`, despite 4 files (Code Review,
   Deployment, Documentation, and now Security's `scanner.py`) importing
   and calling it. Every one of these agents' "try BYOK first" branch therefore always
   raises `ImportError` and silently falls through to the `.env` key today. This task
   gives Security the **same correct structure** its siblings already have — it does
   **not** make BYOK functionally work for any of them. Implementing the real
   `resolve_chat_model` (reading `model_providers`/`model_offerings`) is a separate,
   cross-agent piece of work, out of scope here — flagging it prominently so nobody
   assumes "BYOK support" means BYOK actually works yet.
2. **Semgrep findings dropped CWE.** `run_semgrep_sast` only surfaced `owasp_category`
   from Semgrep's metadata, discarding the `cwe` tags Semgrep also returns. Added a
   `"cwe"` key to the finding dict in `tools/semgrep_sast_tool.py`. Test:
   `test_security_agent_tools.py::test_semgrep_sast_tool_preserves_cwe_tags_alongside_owasp`.
3. **SBOM never populated `vulnerabilities`.** `generate_sbom`'s documented output schema
   promises a `vulnerabilities` count per component; it was never actually computed,
   leaving the model to manually correlate the SBOM against a separate `scan_dependencies`
   run itself. `scan_dependencies` now caches its parsed Trivy findings onto the session
   (`ScanSessionState.last_trivy_findings`, `config/session_state.py`); `generate_sbom`
   cross-references each component against that cache by package name + installed
   version (`tools/security_tools.py`).
   **Updated in the final review's fix wave** to fix a real correctness bug the first pass
   introduced: an empty cache is ambiguous between "scanned, found nothing" and "never
   scanned" — the original fix used `0` for both, so a component could show a confident,
   fabricated-looking `"vulnerabilities": 0` even when `scan_dependencies` had simply
   never run. `last_trivy_findings` now defaults to `None` (not `[]`) as an explicit
   "not yet scanned" sentinel; `generate_sbom` sets `comp["vulnerabilities"] = None` in
   that case (a real `0` only appears once `scan_dependencies` has actually run and found
   no match for that component), and the JSON payload carries a top-level
   `"vulnerability_data": "trivy" | "not_scanned_yet"` marker. `security_prompt.py` now
   also tells the model to call `scan_dependencies` before `generate_sbom`. Tests:
   `test_security_agent_tools.py::test_generate_sbom_cross_references_cached_trivy_findings`,
   `::test_generate_sbom_leaves_vulnerabilities_null_when_no_trivy_scan_ran_yet`,
   `::test_generate_sbom_reports_a_real_zero_when_trivy_ran_but_found_no_match`,
   `::test_scan_dependencies_caches_findings_only_on_a_successful_scan`, plus a new
   end-to-end assertion in `test_security_agent_live_e2e.py` (real Trivy run → real SBOM
   cross-reference, `flask_component["vulnerabilities"] >= 1`, `vulnerability_data ==
   "trivy"`). That test's first scripted turn was split so `scan_dependencies` runs alone
   before the turn that calls `generate_sbom` — LangGraph's `ToolNode` can run same-turn
   tool_calls concurrently, so leaving `scan_dependencies` in the same turn as
   `generate_sbom` would flake between `None` and the real count depending on execution
   order.
   **Known minor gap, deferred (not blocking):** `test_security_agent_live_e2e.py` never
   calls `clear_session("sec-live-e2e-test")`, so a stale session entry leaks in the
   module-level `_registry` for the rest of that pytest process — test-only, no
   production impact, no cross-test assertion failures observed. Worth a one-line fix
   (`clear_session(session_id)` at the end of the test, or converting the session setup
   into a fixture with `yield` + teardown, matching the existing `scan_target_dir`
   fixture's pattern) next time this file is touched.

**Open items before this is a *complete* re-verification** (access-hardening + gating
were already done and shipped in PR #12/#13; the three items above are now fixed —
these are what's left):
1. A working LLM provider key, to prove the model's actual scan judgment/triage, not just
   the scaffolding around it.
2. Declare `semgrep`, `gitleaks` (Go binary, not pip-installable — no clean `uv`/`pip`
   path), and `trivy` (same) properly for a reproducible environment setup.

### 6. Testing — owner role: QA/Tester — `agent_id="testing"` — **access-hardening DONE**
File: `backend/agents_orchestrator/testing_agent/testing_agent_api.py`
- [x] WS handler (`process_user_message_ws`, no leading underscore — differs from the others): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added (edited the ACTIVE decorator; a large commented-out dead duplicate route sits directly above it in the file — left untouched)
- [x] Test: `backend/tests/test_testing_agent_chat_access.py` (3 passed)
- Notes: `user_id` Form field is still used for unrelated session/file-path bookkeeping (`_LAST_SESSION`, `input_directory`) — out of scope for this pass; only the auth identity now comes from `request.state`.

### 7. Deployment — owner role: DevOps Engineer — `agent_id="deployment"` — **access-hardening DONE**
File: `backend/agents_orchestrator/deployment_agent/deployment_standalone_api.py` —
**not** `deployment_agent_api.py` in the same folder, which is a legacy evaluator
mounted at `/sdlc/agent/deployment_orchestrator` and unused by the frontend's chat
(`app/api/chat/route.ts`'s `agentWsPath` maps `"deployment"` to `/sdlc/agent/deployment/ws`,
which is `deployment_standalone_router`).
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_deployment_agent_chat_access.py` (3 passed)
- Notes: like Design, this route previously had no `project_id` Form field at all — added as a new required field, same reasoning (not live yet, nothing depends on the old contract).

### 8. Documentation — owner role: BA (auto-accept; PA fallback) — `agent_id="documentation"` — **DONE (live)**
File: `backend/agents_orchestrator/documentation_agent/documentation_standalone_api.py`
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_documentation_agent_chat_access.py` (3 passed)
- [x] Frontend `builtAgents` includes `"documentation"` — now live and clickable alongside Security's tile
- Notes: no `project_id` Form field here either — reads/writes the in-memory session's already-bound `project_id`, same pattern as Code Review.

**Real-logic verification (2026-08-20), Part 5 steps 3/6, done without a live LLM key**
— same technique and same result as Code Review and Security above: **this agent's
code was NOT a stub either.** `agents_orchestrator/documentation_agent/` is a real
implementation — the graph (`agents/compiler.py`), 9 tools (repo inspection/read/search,
`generate_changelog`, `read_upstream_artifacts`, `save_document`, `open_docs_pr`,
`publish_to_sharepoint`, `list_sharepoint_documents`, `ingest_sharepoint_document`), and
`doc_prompt.py`.
- **New**: `backend/tests/test_documentation_agent_live_e2e.py` — drives the real
  compiled `compiler.app` graph with only the model's `.ainvoke()` scripted (4 turns:
  concurrent `inspect_repo` + `read_upstream_artifacts`, then `generate_changelog`, then
  `save_document`, then a no-tool-calls turn to `END`). Every tool call is real: a real
  git repo with 3 real commits drives `generate_changelog` (real `status`, `commit_count`,
  grouped `### Added`/`### Fixed` sections with the real commit subjects), and a real
  seeded Postgres `Run` row (organization/workspace/project/run, `requirements_payload`
  and `security_artifacts` populated, other artifact columns left null by omission)
  drives `read_upstream_artifacts` — it returns the real seeded
  `requirements.stories[0].id == "US-1"` and `security.signoff.decision == "pass"`,
  and correctly nulls the columns with no upstream artifact (`design is None`, etc.).
  `save_document` actually wrote the file to disk and updated the session's
  `generated_docs`. **Not proven: the model's own judgment** — needs a working LLM key
  (see Open items below).
- **New**: `backend/tests/test_documentation_agent_tools.py` — 13 isolated unit tests,
  deliberately narrower/faster than the live_e2e test: 3 `generate_changelog` edge cases
  (unconventional-prefix bucketing, merge-commit exclusion, the no-commits case), the
  `read_upstream_artifacts` all-null case with no tenant/project bound, 3 `save_document`
  guards (filename sanitization, replace-not-duplicate on the same filename, empty-content
  refusal), 2 `open_docs_pr` precondition checks (no documents generated, no prepared
  target), 2 SharePoint tools' graceful "not connected" degradation, and the same
  `_resolve_model` BYOK-first/fallback regression pair Security carries. Two of these
  tests corrected a wrong assumption made while planning them, not a bug in the tool
  itself:
  1. The no-commits case was planned as `status == "ok"`, `commit_count == 0`. Actual
     behavior: `git log` exits 128 against a zero-commit repo, which `generate_changelog`
     surfaces as `status == "error"`, `changelog == ""` — the test
     (`test_generate_changelog_with_no_commits_yet_returns_error_gracefully`) was
     corrected to match reality, not the tool.
  2. Patching `shared.services.model_resolver.resolve_chat_model` and
     `shared.services.notification_targets.sharepoint_target` directly with `patch(...)`
     as originally planned fails outright — neither target exists to patch (see the
     `resolve_chat_model` caveat below), so `unittest.mock.patch` has nothing to attach
     to. Corrected to `patch.dict(sys.modules, {...})`, mocking the whole module at
     import time, mirroring Security's existing regression-test pattern.
- **RTM structural-traceability finding.** The `rtm` deliverable's original prompt text
  told the model to "build it fully from upstream artifacts when present" without
  qualification — but inspecting the actual upstream artifact models
  (`shared/models/requirements.py`, `shared/models/code_review.py`) shows only two of
  the RTM's five non-Requirement columns are backed by a real, structurally matchable
  ID: Requirements itself (`UserStory.id` / `AcceptanceCriteria.id`) and Code Review
  (`CoverageEntry.ac_id`, which references an AC id when populated). Design,
  Development, Testing, and Security have no requirement-ID field anywhere in their
  artifacts — any "match" the model draws for those columns can only ever be a textual
  inference (e.g. a story title echoed in a design doc's prose), never a structural one,
  and the old prompt text let the model present both kinds of "match" with the same
  confidence. **Fixed**: `backend/agents_orchestrator/documentation_agent/prompts/doc_prompt.py`'s
  `rtm` bullet now names the two structurally-verified columns explicitly, requires
  "N/A (no upstream artifact)" for the other four when nothing exists, and requires an
  exact "Inferred — not structurally traceable, verify manually: " prefix on any
  textual correlation the model reports for those four — never omitted, never presented
  as equivalent to a real ID match. Verified the prompt still imports cleanly and
  contains the new marker text after the edit.
- `read_repo_file` / `search_repo` behavior matches the pattern already verified for
  Code Review and Security (real reads, real search) — not independently re-spot-checked
  in this pass since the live_e2e test already exercises `inspect_repo` end to end.

**SharePoint is entirely dead code (found in the whole-branch review, 2026-08-21) —
documented, not fixed, in this pass.** `doc_tools.py`'s three SharePoint tools
(`publish_to_sharepoint`, `list_sharepoint_documents`, `ingest_sharepoint_document`) all
route through `_sharepoint_session()`, which does
`from shared.services.notification_targets import sharepoint_target` inside a
try/except. That module — `shared/services/notification_targets.py` — does not exist
as a file anywhere in the backend (grep-confirmed, and confirmed by running
`cd backend && uv run python -c "import shared.services.notification_targets"`, which
raises `ModuleNotFoundError`). So all three SharePoint tools currently always fail in
production with `"ERROR reaching SharePoint: ModuleNotFoundError"` — not the friendlier
"SharePoint is not connected for this tenant" message a caller might reasonably expect
from reading `_sharepoint_session`'s code. The same missing import also breaks
`shared/routers/documentation_workspace.py`'s `list_doc_connectors` endpoint's
SharePoint-availability check (it's wrapped in its own try/except, so it silently
reports `available: false` regardless of any real configuration, rather than raising).
Note: the unit tests `test_publish_to_sharepoint_reports_not_connected_cleanly` and
`test_list_sharepoint_documents_reports_not_connected_cleanly`
(`backend/tests/test_documentation_agent_tools.py`) use
`patch.dict(sys.modules, {"shared.services.notification_targets": ...})` to inject a
fake module in place of the real (missing) one — this is the correct way to test the
"not connected" branch's logic in isolation, but it means these tests **cannot** and do
not detect that the real module doesn't exist. Passing tests here do not mean
SharePoint publishing works. Implementing `shared/services/notification_targets.py` for
real is out of scope for this pass.

**Gated actions are enforced by prompt text only (found in the whole-branch review,
2026-08-21).** `open_docs_pr` and `publish_to_sharepoint` are gated only by the system
prompt's instruction ("only call this when the user explicitly asks") —
`agents/compiler.py`'s tool node (`make_dynamic_tool_node`, in
`shared/tools/mcp_runtime.py`) has no per-tool authorization check; whatever tool_call
the model emits, it executes. This is pre-existing and matches every sibling agent
(Code Review, Security) — not a regression introduced by this plan — but it's worth
recording now that this branch makes the Documentation agent reachable by real users,
since it reads repo files and (if SharePoint were ever wired up) external document text
into model context, both plausible prompt-injection surfaces. No code fix in this
branch — recorded here as a known limitation for a future cross-agent
tool-authorization pass.

**Caveat, matching Security's section's own honesty about this:**
`shared.services.model_resolver.resolve_chat_model` still does not exist anywhere in the
backend (same grep-verified fact Security's section already documents — zero matches for
`def resolve_chat_model`). Documentation's `_resolve_model` already had the correct
try-BYOK-first-then-fall-back structure and already correctly preserves the caller's
`model_id` on fallback (verified by
`test_resolve_model_falls_back_to_raw_chat_anthropic_preserving_model_id` — unlike Code
Review's and pre-fix Security's, it was never hardcoding the fallback model). This task
only added regression tests locking in that already-correct behavior; it did **not**
implement the missing `resolve_chat_model` resolver. BYOK still does not functionally
work for Documentation, or for any of the other three agents that import it, until that
resolver is actually written — a separate, cross-agent piece of work, out of scope here.

**Known minor gap, deferred, already ledgered in Task 1's own review (not blocking):**
the live_e2e test's `save_document` call writes a real document file to a real,
gitignored path under `backend/files/generated-docs/`, and the test has no cleanup step
for it. Harmless — the path is gitignored and nothing reads stale files there — but
repeated local/CI runs accumulate unbounded clutter in that directory over time. Worth a
small follow-up fixture (temp-dir redirect or a teardown `unlink`) next time this file is
touched.

**Open items before this is a *complete* re-verification** (access-hardening and the go
-live flip are done; the items above are now fixed):
1. A working LLM provider key, to prove the model's actual documentation judgment (what
   it chooses to include, how it phrases inferred RTM correlations), not just the
   scaffolding and prompt rules around it. This includes the RTM's exact
   `"Inferred — not structurally traceable, verify manually: "` prefix (em dash
   included): it is a prompt instruction, not something any code validates, so with no
   working LLM key in this environment that guarantee is aspirational until verified
   against a real model call — same category as the rest of this item, not a separate
   gap.
2. The real `resolve_chat_model` implementation (see caveat above) — cross-agent, not
   Documentation-specific.
3. The `save_document` live_e2e cleanup noted above.
4. `shared/models/design.py`'s `DesignArtifacts.linked_work_item_ids` field is, in
   principle, exactly the kind of requirement-ID link the RTM prompt's Design column is
   told it doesn't have — but grep-confirmed (`grep -rn "linked_work_item_ids"
   backend/`) it is never assigned anywhere in the backend today, only declared (plus
   its own deprecated alias, `linked_ado_ids`, in a comment). If this field is ever
   wired up to be populated for real, the RTM prompt fix above would need revisiting,
   since it would then be forcing a genuine structural link to be labeled "Inferred".

---

## Next stage (not started, not in scope of this pass)

- **Orchestrator cockpit rebuild** (`frontend/app/(app)/projects/[id]/orchestrator/page.tsx`,
  `frontend/app/(app)/orchestrator/page.tsx`, `components/orchestrator/cockpit.tsx`) —
  design doc §2.3. Explicitly deferred — do not start until the access-hardening pass
  above is complete for all 8 agents.
- **Full LangGraph rebuild** of each of the 8 agents per Part 5 steps 3 & 6 — separate,
  much larger effort per agent, after this access-hardening pass and after each agent's
  actual behavior has been reviewed against the PRD (§§21–25) and this document's Part 3
  table for that agent.
- **Portfolios 2–4** (Modernization, RPA & Infra Migration, Data Engineering) — nothing
  exists yet; `TRACK_PORTFOLIOS` has empty lists for all three tracks today. Not started.

---

*Last updated: 2026-08-31. Keep this file current as each agent's row changes — this is
the shared source of truth across everyone working on Portfolio 1, not a snapshot.*
