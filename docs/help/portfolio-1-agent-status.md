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

### 4. Code Review — owner role: Architect — `agent_id="code_review"` — **access-hardening DONE**
File: `backend/agents_orchestrator/code_review_agent/code_review_agent_api.py`
- [x] WS handler (`_process_ws_message`): `assert_agent_access_for_chat` added, checked every message
- [x] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access_for_chat` added
- [x] Test: `backend/tests/test_code_review_agent_chat_access.py` (3 passed)
- Notes: this route has no `project_id` Form field either — the review target is bound out-of-band by a separate `POST /review/prepare` call into an in-memory session; the REST check reads the session's already-bound `project_id` instead.

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
