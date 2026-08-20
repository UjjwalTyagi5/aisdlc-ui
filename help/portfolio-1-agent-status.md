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

### 3. Development — owner role: Architect (Developer builds) — `agent_id="development"` — **access-hardening DONE**
File: `backend/agents_orchestrator/development_agent/development_agent_api.py`
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_development_agent_chat_access.py` (3 passed)
- Notes: no `project_id` Form field here either — project id only exists inside the `pipeline_context` JSON field, reused via the existing `_lf_pid` extraction rather than adding a new field.

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
   backend** — zero matches for `def resolve_chat_model`, despite 5 files (Code Review,
   Deployment, Documentation, and now Security's `scanner.py`/its new test) importing
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
   version (`tools/security_tools.py`) — `0` when `scan_dependencies` hasn't run yet this
   session or nothing matches, never fabricated. Tests:
   `test_security_agent_tools.py::test_generate_sbom_cross_references_cached_trivy_findings`,
   `::test_generate_sbom_reports_zero_vulnerabilities_when_no_trivy_scan_ran_yet`, plus a
   new end-to-end assertion in `test_security_agent_live_e2e.py` (real Trivy run → real
   SBOM cross-reference, `flask_component["vulnerabilities"] >= 1`). That test's first
   scripted turn was split so `scan_dependencies` runs alone before the turn that calls
   `generate_sbom` — LangGraph's `ToolNode` can run same-turn tool_calls concurrently, so
   leaving `scan_dependencies` in the same turn as `generate_sbom` would flake between 0
   and the real count depending on execution order.

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

### 8. Documentation — owner role: BA (auto-accept; PA fallback) — `agent_id="documentation"` — **access-hardening DONE**
File: `backend/agents_orchestrator/documentation_agent/documentation_standalone_api.py`
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_documentation_agent_chat_access.py` (3 passed)
- Notes: no `project_id` Form field here either — reads/writes the in-memory session's already-bound `project_id`, same pattern as Code Review.

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

*Last updated: 2026-08-20. Keep this file current as each agent's row changes — this is
the shared source of truth across everyone working on Portfolio 1, not a snapshot.*
