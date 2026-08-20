# Portfolio 1 (Greenfield/Brownfield) — Agent Build Status

**Purpose.** One shared, living reference for everyone building out the 8 Portfolio-1
agents (Track 1 — Greenfield, Track 2 — Enhancement & Support, which reuses the same 8).
Each agent has its own owner and its own section below — update your section as you
work. Full background: [`multi-track-agent-access-design.md`](./multi-track-agent-access-design.md)
in this same folder (Parts 1–3 for what each agent does and who owns it; Part 5 for the
full build/rebuild checklist this status table tracks against).

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
- [ ] `POST /runs`'s `active_agents` list validated against the caller's per-agent access (`backend/shared/routers/runs.py`) — **tracked here, in progress**

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
2. Its WS message handler resolves `project_id` via `resolve_project(...)` before
   trusting it, then calls `assert_agent_access(..., agent_id="<id>")` on **every**
   message, not just the first (a session can be reused across projects client-side).
3. Its REST `POST .../chat/` route stops trusting the `user_id` Form field for identity —
   pulls `request.state.user_id` / `request.state.tenant_id` instead — resolves
   `project_id` the same way, and calls `assert_agent_access` before doing any work.
   `user_id` stays in the form signature for wire compatibility but is documented as
   unused for auth (see Security's `chat()` for the exact comment to mirror).
4. A quick live/test check that an org_admin (holds `admin:*`, zero default agent access)
   gets denied, and the agent's owning role gets through — mirroring
   `backend/tests/test_security_agent_chat_access.py`.

Reference implementation, already merged: `backend/agents_orchestrator/security_agent/security_agent_api.py`
(`_process_ws_message`, `chat()`) + `backend/tests/test_security_agent_chat_access.py`.

---

## Per-agent status

### 1. Requirements — owner role: BA — `agent_id="requirements"`
File: `backend/agents_orchestrator/requirements_agent/requirements_agent_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

### 2. Design — owner role: Architect — `agent_id="design"`
File: `backend/agents_orchestrator/design_architecture_agent/design_architecture_agent_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

### 3. Development — owner role: Architect (Developer builds) — `agent_id="development"`
File: `backend/agents_orchestrator/development_agent/development_agent_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

### 4. Code Review — owner role: Architect — `agent_id="code_review"`
File: `backend/agents_orchestrator/code_review_agent/code_review_agent_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

### 5. Security — owner role: Security Engineer — `agent_id="security"` — **DONE (reference implementation)**
File: `backend/agents_orchestrator/security_agent/security_agent_api.py`
- [x] WS handler: `assert_agent_access` added, checked on every message
- [x] REST `/chat/`: `request.state.user_id` used, `user_id` Form field no longer trusted
- [x] Project-membership check added on both routes (404 if caller isn't on the project)
- [x] `security_workspace_router` gated with `Depends(require_agent_access("security"))`
- [x] Tests: `backend/tests/test_security_agent_chat_access.py`, `backend/tests/test_security_workspace_agent_access.py`
- [x] Frontend `builtAgents` includes `"security"` — the one live, clickable tile today
- Landed in: PR #12, PR #13 (both merged/open against `main`)

### 6. Testing — owner role: QA/Tester — `agent_id="testing"`
File: `backend/agents_orchestrator/testing_agent/testing_agent_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

### 7. Deployment — owner role: DevOps Engineer — `agent_id="deployment"`
File: `backend/agents_orchestrator/deployment_agent/deployment_agent_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

### 8. Documentation — owner role: BA (auto-accept; PA fallback) — `agent_id="documentation"`
File: `backend/agents_orchestrator/documentation_agent/documentation_standalone_api.py`
- [ ] WS handler: `assert_agent_access` added
- [ ] REST `/chat/`: stopped trusting client `user_id`, `assert_agent_access` added
- [ ] Test added/passing
- Assigned to: _TBD_
- Notes:

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
