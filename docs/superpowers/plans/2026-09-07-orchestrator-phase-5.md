# Orchestrator Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the two old engines and the Copilot surface, and make the Orchestrator's left rail real server-backed chat history that can be reopened and continued.

**Architecture:** 5A moves the four `lib/copilot/` modules the Orchestrator depends on into `lib/orchestrator/` FIRST, so every later deletion is safe, then deletes the Copilot frontend, the whole `agents_orchestrator/orchestrator/` package, and the Copilot REST endpoints. 5B wires `orchestrator2/ws.py` to the `conversation_service` every standalone agent already uses (`session_id == run_id`) and repoints the rail from `localStorage` to the server.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / LangGraph; Next.js 15 / React 19 / TypeScript / Zod / Zustand / Vitest.

**Spec:** `docs/superpowers/specs/2026-09-07-orchestrator-phase-5-design.md`

## Global Constraints

- **The Orchestrator's agents are not the standalone agents.** Deliverables are never written to `artifacts`, and nothing in `orchestrator2` reads the run's `*_artifacts` columns.
- **Say "Business Unit" in prose, never "workspace".** Code identifiers still literally say `workspace`.
- **Project scope is load-bearing.** `project_id` and `user_id` come from the verified `runs` row / ticket claim, never from a client frame, and neither has a default.
- **RBAC must not regress.** Project Admin only; per-project, not per-tenant. Every new read carries an explicit `tenant_id` predicate — RLS is inert here (the app connects as a `rolbypassrls` superuser).
- **The nine agent ids** are `requirements design plan development code_review security testing deployment documentation`. `plan` is the **Project Manager agent** in all user-facing text.
- Backend tests: `cd backend && uv run python -m pytest ...` — bare `uv run pytest` fails with `ModuleNotFoundError: config`.
- `npm run lint` has 2 pre-existing errors unrelated to this branch; lint your own files with `npx eslint components/orchestrator lib/orchestrator`.
- **A green suite is not evidence.** Break the implementation and confirm a test fails. A SURVIVED mutation is not a result until you show the anchor is gone — and **"the file changed" is not "the line I meant changed"**: assert the anchor is UNIQUE before mutating, and probe the function under test afterwards. `git diff --numstat` proves nothing for an untracked file.
- **Deletion has a failure mode unit tests cannot catch:** a removed import compiles until the page renders. 5A is not done until `npm run build` passes and the Orchestrator loads live.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/lib/orchestrator/artifacts.ts` (moved) | Artifact/kind vocabulary + the four artifact events |
| `frontend/lib/orchestrator/stages.ts` (moved) | `AGENT_STAGES`, `stageLabel`, `ownerRoleLabel` |
| `frontend/lib/orchestrator/chat-types.ts` (moved) | `ChoiceCard`, `GateState`, `ActivityItem`, `ConnState` |
| `frontend/app/api/chat/route.ts` (modify) | Refuse an unmapped agent |
| `frontend/app/(app)/runs/page.tsx`, `components/app/project-runs-table.tsx` (modify) | Point history at the read-only conversation view |
| `backend/agents_orchestrator/orchestrator/` (delete) | The whole retired package |
| `backend/process_api.py` (modify) | Drop two mounts; `ws_max_size`; extended boot sweep |
| `backend/shared/authz/dependency.py` (modify) | Boot sweep covers WebSocket routes |
| `backend/agents_orchestrator/orchestrator2/ws.py` (modify) | Frame-size refusal; persist turns |
| `backend/agents_orchestrator/orchestrator2/sessions.py` (create) | Session creation + turn persistence for the Orchestrator |
| `frontend/components/orchestrator/cockpit.tsx` (modify) | Rail reads from the server; opening a session restores it |

---

# PART 5A — RETIREMENT

### Task 1: Move the shared modules out of `lib/copilot`

Done FIRST so every later deletion is safe. Nothing is deleted in this task.

**Files:**
- Create: `frontend/lib/orchestrator/{artifacts,stages,chat-types}.ts` (moved content)
- Modify: `frontend/lib/copilot/{artifacts,stages,types}.ts` → deleted at the end of this task
- Modify (imports): `components/orchestrator/{artifacts-panel,artifact-viewer,choice-card}.tsx`, `lib/orchestrator/{protocol,deliverables,use-orchestrator-socket}.ts`, `components/orchestrator/__tests__/artifacts-panel-approver.test.tsx`, and the four `components/copilot/*.tsx` files (still alive until Task 4)
- Test: `frontend/lib/orchestrator/__tests__/no-copilot-imports.test.ts`

**Interfaces:**
- Produces: `AGENT_STAGES` (renamed from `COPILOT_STAGES`), `stageLabel`, `ownerRoleLabel`, `ArtifactKind`, `Artifact`, `ARTIFACT_EVENTS`, `rendererFor`, `ArtifactsRead`, `TranscriptRead`, `ChoiceCard`, `GateState`, `ActivityItem` (renamed from `CopilotActivityItem`), `ConnState` (renamed from `CopilotConnState`) — all from `@/lib/orchestrator/*`

- [ ] **Step 1: Write the failing test**

`frontend/lib/orchestrator/__tests__/no-copilot-imports.test.ts`:

```ts
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The Orchestrator must not import from `lib/copilot`.
 *
 * Phase 5 deletes that directory. Until this test existed, four Orchestrator modules
 * imported from it — so "delete the Copilot" would have broken the surface that
 * replaces it, and only at render time, because a missing module still typechecks
 * until something resolves it.
 */
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.tsx?$/.test(entry) ? [full] : [];
  });
}

describe("the Orchestrator owns its own vocabulary", () => {
  it("imports nothing from lib/copilot", () => {
    const offenders = [...walk("lib/orchestrator"), ...walk("components/orchestrator")]
      .filter((f) => readFileSync(f, "utf8").includes("@/lib/copilot/"));
    expect(offenders).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/no-copilot-imports.test.ts`
Expected: FAIL, listing the seven Orchestrator files that still import `@/lib/copilot/`.

- [ ] **Step 3: Move the files with git so history follows**

```bash
cd frontend
git mv lib/copilot/artifacts.ts lib/orchestrator/artifacts.ts
git mv lib/copilot/stages.ts   lib/orchestrator/stages.ts
git mv lib/copilot/types.ts    lib/orchestrator/chat-types.ts
```

`types.ts` becomes `chat-types.ts` because `lib/orchestrator/types.ts` already exists (it holds `OrchestratorMessage`, `PHASE_FOR_AGENT`, `agentLabel`). Merging two unrelated files to save a name would make both harder to read.

- [ ] **Step 4: Move the two types out of `use-copilot.ts`**

`CopilotActivityItem` and `CopilotConnState` are types only — the Orchestrator never used the hook. Cut both declarations from `lib/copilot/use-copilot.ts` and paste them into `lib/orchestrator/chat-types.ts`, renamed:

```ts
/** One row in the Activity feed: a tool call, a thought, or a turn boundary. */
export interface ActivityItem {
  id: string;
  ts: string;
  kind: "tool" | "thinking" | "stage" | "turn";
  label: string;
  status?: "running" | "done";
}

/** WebSocket connection state, as the panel's banner reads it. */
export type ConnState = "idle" | "connecting" | "connected" | "reconnecting" | "closed";
```

Verify both against the originals before deleting them — copy the real field list rather than this sketch if it differs, and leave `use-copilot.ts` importing them back from `@/lib/orchestrator/chat-types` so the Copilot still builds until Task 4.

- [ ] **Step 5: Rename `COPILOT_STAGES` → `AGENT_STAGES`**

In `lib/orchestrator/stages.ts`, rename the exported constant and update its docstring. A name saying "Copilot" inside the surviving surface is exactly the prose-versus-reality drift this branch has corrected nine times.

```bash
cd frontend && grep -rln "COPILOT_STAGES" --include=*.ts --include=*.tsx . | grep -v node_modules
```
Update every hit.

- [ ] **Step 6: Update every import**

```bash
cd frontend
grep -rl "@/lib/copilot/artifacts" --include=*.ts --include=*.tsx . | grep -v node_modules \
  | xargs sed -i 's#@/lib/copilot/artifacts#@/lib/orchestrator/artifacts#g'
grep -rl "@/lib/copilot/stages" --include=*.ts --include=*.tsx . | grep -v node_modules \
  | xargs sed -i 's#@/lib/copilot/stages#@/lib/orchestrator/stages#g'
grep -rl "@/lib/copilot/types" --include=*.ts --include=*.tsx . | grep -v node_modules \
  | xargs sed -i 's#@/lib/copilot/types#@/lib/orchestrator/chat-types#g'
```

Then fix the two type imports by hand (`CopilotActivityItem` → `ActivityItem`, `CopilotConnState` → `ConnState`, sourced from `@/lib/orchestrator/chat-types`) in `components/orchestrator/artifacts-panel.tsx` and `lib/orchestrator/use-orchestrator-socket.ts`.

- [ ] **Step 7: Update the backend contract tests that read these paths**

`backend/tests/orchestrator2/test_event_field_shapes.py` reads
`frontend/lib/copilot/artifacts.ts` for the renderable-kind list. Point it at
`frontend/lib/orchestrator/artifacts.ts`.

- [ ] **Step 8: Run everything**

```bash
cd frontend && npx vitest run && npm run typecheck && npx eslint components/orchestrator lib/orchestrator
cd ../backend && uv run python -m pytest tests/orchestrator2/ -q
```
Expected: all green; the new `no-copilot-imports` test now passes.

- [ ] **Step 9: Prove the guard bites**

Add `import { ArtifactKind } from "@/lib/copilot/artifacts";` to `lib/orchestrator/deliverables.ts`, re-run the new test, confirm it FAILS naming that file, then remove it in a `finally` and confirm green.

- [ ] **Step 10: Commit**

```bash
git add -A frontend backend/tests/orchestrator2/test_event_field_shapes.py
git commit -m "refactor(orchestrator): own the artifact and stage vocabulary

Moved with git mv so history follows. lib/copilot is deleted in this phase, and
four Orchestrator modules imported from it — so deleting the Copilot would have
broken the surface that replaces it, at render time rather than at build time.

COPILOT_STAGES becomes AGENT_STAGES: a name saying Copilot inside the surviving
surface is the prose-versus-reality drift this branch has corrected nine times."
```

---

### Task 2: `/api/chat` refuses an unmapped agent

**Files:**
- Modify: `frontend/app/api/chat/route.ts:70-75`
- Test: `frontend/app/api/__tests__/chat-agent-map.test.ts`

**Interfaces:**
- Produces: `agentWsPath(agent?: string): string | null` — `null` for an unmapped agent (was: the legacy orchestrator path)

- [ ] **Step 1: Write the failing test**

Append to `frontend/app/api/__tests__/chat-agent-map.test.ts`:

```ts
describe("after Phase 5 retired the legacy orchestrator engine", () => {
  it("maps all nine agents", () => {
    for (const agent of ["requirements", "design", "plan", "development",
      "code_review", "security", "testing", "deployment", "documentation"]) {
      expect(agentWsPath(agent), `${agent} is unmapped`).toBeTruthy();
    }
  });

  it("refuses an unmapped agent instead of routing it somewhere", () => {
    // It used to fall through to /sdlc/agent/orchestrator/ws, which no longer
    // exists. Falling through to ANY other engine would mean an unknown agent
    // quietly answering as something else — the failure class this rebuild removed.
    expect(agentWsPath("marketing")).toBeNull();
    expect(agentWsPath(undefined)).toBeNull();
  });

  it("names no retired engine anywhere in the table", () => {
    for (const agent of ["requirements", "design", "plan", "development",
      "code_review", "security", "testing", "deployment", "documentation"]) {
      expect(agentWsPath(agent)).not.toContain("/sdlc/agent/orchestrator/ws");
      expect(agentWsPath(agent)).not.toContain("/sdlc/agent/copilot/ws");
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run app/api/__tests__/chat-agent-map.test.ts`
Expected: FAIL — `agentWsPath("marketing")` returns the legacy path, not `null`.

- [ ] **Step 3: Implement**

In `frontend/app/api/chat/route.ts`, change the signature to `string | null` and replace the default branch:

```ts
    // NO FALLBACK. This used to return /sdlc/agent/orchestrator/ws, the engine Phase 5
    // retired. Falling through to any other engine would let an unmapped agent answer
    // as something else — silently, and looking exactly like a working reply. Every
    // caller (useAgentChat) passes an explicit agent, so reaching here is a bug, and
    // it is reported as one.
    default:
      return null;
  }
}
```

At the POST handler's call site, refuse the request rather than opening a socket:

```ts
  const wsPath = agentWsPath(body.agent);
  if (!wsPath) {
    return Response.json(
      { code: "unknown_agent", detail: `no agent named ${body.agent ?? "(none)"}` },
      { status: 400 },
    );
  }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run app/api/ && npm run typecheck
```

- [ ] **Step 5: Prove the refusal test can fail**

Change `return null;` back to `return "/sdlc/agent/orchestrator/ws";`, re-run, confirm `refuses an unmapped agent` FAILS. Restore in a `finally`.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/api/chat/route.ts frontend/app/api/__tests__/chat-agent-map.test.ts
git commit -m "fix(chat): refuse an unmapped agent instead of routing it to a retired engine

The default fell through to /sdlc/agent/orchestrator/ws, which Phase 5 deletes.
Every caller passes an explicit agent, so reaching the default is a bug — and an
unknown agent quietly answering as something else is the failure class this whole
rebuild exists to remove."
```

---

### Task 3: Run history points at the read-only conversation view

**Files:**
- Modify: `frontend/app/(app)/runs/page.tsx:283`
- Modify: `frontend/components/app/project-runs-table.tsx:99`
- Test: `frontend/app/__tests__/runs-history-link.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/app/__tests__/runs-history-link.test.tsx`:

```tsx
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The runs list is the platform-wide run table — pipeline runs, webhook runs and
 * agent runs alike. It used to open a row in the Copilot, which Phase 5 deletes.
 *
 * It now opens the read-only conversation view: history is for reading, and the
 * Orchestrator's own rail is for continuing a chat (Phase 5B). A source check
 * rather than a render test, because the failure being prevented is a link to a
 * route that no longer exists — which renders perfectly until it is clicked.
 */
const FILES = [
  "app/(app)/runs/page.tsx",
  "components/app/project-runs-table.tsx",
];

describe("run history", () => {
  it("never links to the retired Copilot page", () => {
    for (const f of FILES) {
      expect(readFileSync(f, "utf8"), f).not.toContain("/copilot?run=");
    }
  });

  it("links to the read-only conversation view", () => {
    for (const f of FILES) {
      expect(readFileSync(f, "utf8"), f).toContain("/conversation");
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run app/__tests__/runs-history-link.test.tsx`
Expected: FAIL on both assertions for both files.

- [ ] **Step 3: Implement**

`app/(app)/runs/page.tsx:283`:
```tsx
            onRowClick={(r) => router.push(`/runs/${r.id}/conversation`)}
```

`components/app/project-runs-table.tsx:99`:
```tsx
                  href={`/runs/${r.id}/conversation`}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run app/ components/ && npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
git add frontend/app frontend/components
git commit -m "refactor(runs): history opens the read-only conversation view

Both links opened a run in the Copilot, which Phase 5 deletes — ruling R10 kept
them because nothing replaced opening a past run. The runs list is the
platform-wide run table (pipeline and webhook runs too), so reading is what it
should offer; continuing a chat belongs to the Orchestrator's own rail."
```

---

### Task 4: Delete the Copilot frontend

**Files:**
- Delete: `frontend/app/(app)/projects/[id]/copilot/`, `frontend/components/copilot/`, `frontend/lib/copilot/`, `frontend/app/api/copilot/`, `frontend/app/api/runs/[id]/copilot/`
- Test: `frontend/lib/orchestrator/__tests__/no-copilot-surface.test.ts`

- [ ] **Step 1: Write the failing test**

`frontend/lib/orchestrator/__tests__/no-copilot-surface.test.ts`:

```ts
import { existsSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The Copilot is gone. Phase 1 unlinked it but deliberately left it reachable,
 * because it was the only surface actually running agents and deleting it then
 * would have left the product with no working orchestration. The Orchestrator now
 * runs all nine agents, persists Deliverables and keeps its own history, so the
 * reason to keep it has expired.
 */
const GONE = [
  "app/(app)/projects/[id]/copilot",
  "components/copilot",
  "lib/copilot",
  "app/api/copilot",
  "app/api/runs/[id]/copilot",
];

describe("the Copilot surface", () => {
  it.each(GONE)("%s is deleted", (path) => {
    expect(existsSync(path)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/no-copilot-surface.test.ts`
Expected: FAIL — all five paths still exist.

- [ ] **Step 3: Delete**

```bash
cd frontend
git rm -r "app/(app)/projects/[id]/copilot" components/copilot lib/copilot \
          app/api/copilot "app/api/runs/[id]/copilot"
```

- [ ] **Step 4: Run everything, including a real build**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run build
```

`npm run build` is REQUIRED here and is the point of the task: it resolves every
import in every route, which is the only thing that catches a deletion that removed
something still referenced. `vitest` and `tsc` both pass on code no page imports.

- [ ] **Step 5: Commit**

```bash
git add -A frontend
git commit -m "feat(orchestrator): delete the Copilot surface

Phase 1 unlinked it and deliberately left it reachable — it was the only surface
actually running agents, and deleting it then would have left the product with no
working orchestration for the length of the backend work. The Orchestrator now runs
all nine agents, persists Deliverables, and Phase 5B gives it real history, so the
reason to keep it has expired.

Verified with npm run build, not only vitest and tsc: a deletion that removes
something still imported passes both of those and fails at render."
```

---

### Task 5: Delete the backend engines

**Files:**
- Delete: `backend/agents_orchestrator/orchestrator/` (the whole package), `backend/tests/copilot/`
- Modify: `backend/process_api.py:24-25` and the two `include_router` calls
- Modify: `backend/shared/routers/runs.py` — remove `copilot_advance`, `copilot_set_stage`, `copilot_cancel_turn`
- Modify: `backend/tests/test_gate_self_approval.py` — drop its `copilot_api` import
- Modify: `backend/agents_orchestrator/orchestrator2/router.py` — reword the five `stage_switch.py` mentions
- Test: `backend/tests/orchestrator2/test_old_engines_are_gone.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_old_engines_are_gone.py`:

```python
"""The old engines are retired.

`copilot_api.py` ran three of nine agents with a system prompt and had no `plan`
branch at all, failing soft both times — the defect that prompted this rebuild.
`orchestrator_api.py` was reference-only from the start. Both stayed alive through
Phases 1-4 on purpose, because until Phase 4 the Copilot was the only surface that
actually ran agents.
"""
import importlib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module", [
    "agents_orchestrator.orchestrator.copilot_api",
    "agents_orchestrator.orchestrator.orchestrator_api",
    "agents_orchestrator.orchestrator.copilot_cards",
    "agents_orchestrator.orchestrator.stage_switch",
])
def test_the_module_cannot_be_imported(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_the_package_directory_is_gone():
    assert not (BACKEND / "agents_orchestrator" / "orchestrator").exists()
    assert not (BACKEND / "tests" / "copilot").exists()


def test_nothing_still_imports_them():
    """A stale import is a boot failure, not a test failure, so it is worth a check
    that reads the tree rather than waiting for the app to start."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            if "orchestrator.copilot_api" in stripped or \
               "orchestrator.orchestrator_api" in stripped or \
               "orchestrator.copilot_cards" in stripped or \
               "orchestrator.stage_switch" in stripped:
                offenders.append(f"{path.relative_to(BACKEND)}: {stripped}")
    assert offenders == [], "\n".join(offenders)


def test_the_copilot_rest_endpoints_are_gone():
    from shared.routers.runs import runs_router
    paths = {r.path for r in runs_router.routes}
    for gone in ("/{run_id}/copilot/advance", "/{run_id}/copilot/set-stage",
                 "/{run_id}/copilot/cancel-turn"):
        assert gone not in paths, f"{gone} still registered"


def test_the_orchestrator2_socket_is_still_mounted():
    """The one that must SURVIVE. Deleting the wrong mount is the obvious way to
    break this task, and it would not show up in any other test here."""
    import process_api

    paths = {getattr(r, "path", "") for r in process_api.app.routes}
    assert any("orchestrator2" in p for p in paths), sorted(p for p in paths if "agent" in p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_old_engines_are_gone.py -q`
Expected: FAIL — the modules import fine and the directories exist.

- [ ] **Step 3: Remove the mounts**

In `backend/process_api.py`, delete lines 24-25:

```python
from agents_orchestrator.orchestrator.orchestrator_api import orchestrator_router
from agents_orchestrator.orchestrator.copilot_api import copilot_router
```

and the two matching `app.include_router(...)` calls. Find them with:

```bash
cd backend && grep -n "orchestrator_router\|copilot_router" process_api.py
```

- [ ] **Step 4: Remove the Copilot REST endpoints**

In `backend/shared/routers/runs.py`, delete `copilot_advance`, `copilot_set_stage` and
`copilot_cancel_turn` (around lines 751, 847, 928) together with their decorators, and
the `from agents_orchestrator.orchestrator.copilot_api import request_turn_cancel`
inside `copilot_cancel_turn`. Check nothing else in the file references them:

```bash
cd backend && grep -n "copilot" shared/routers/runs.py
```

- [ ] **Step 5: Delete the packages**

```bash
cd backend
git rm -r agents_orchestrator/orchestrator tests/copilot
```

Then drop the `copilot_api` import in `tests/test_gate_self_approval.py` (line ~30) and
whatever assertion depended on it; if the test's entire subject was Copilot gate
behaviour, delete the test with a commit-message note rather than leaving it asserting
nothing.

- [ ] **Step 6: Reword the `stage_switch.py` mentions**

`agents_orchestrator/orchestrator2/router.py` names `stage_switch.py` five times, all in
prose explaining why this router is built differently. Keep the reasoning; change each
mention to say the file was retired in Phase 5, e.g.:

```python
#   · nothing about order. The engine this replaced advanced by
#     `STAGE_ORDER.index(active) + 1` (`stage_switch.py`, deleted in Phase 5)
```

A comment pointing a reader at a path that no longer exists is the same defect as one
asserting a guarantee the code does not provide.

- [ ] **Step 7: Run everything**

```bash
cd backend && uv run python -m pytest tests/orchestrator2/ -q
cd backend && uv run python -m pytest tests/ -q
```

The full suite has 22 known pre-existing failures (RLS inert, plus two live-E2E fixtures
failing on `invalid UUID 'test-tenant'`). Compare against that baseline; anything NEW is
yours. Any `tests/copilot`-shaped import error means Step 5 missed a reference.

- [ ] **Step 8: Confirm the app still boots**

```bash
cd backend && uv run python -c "import process_api; print('boot import OK')"
```

Then start it and confirm the boot scan passes:
```bash
cd backend && uv run uvicorn process_api:app --host 127.0.0.1 --port 8004
```
Expected in the log: `D-05 route-coverage boot scan: ... no offenders`, and
`rbac catalogue: verified`. A missing mount does NOT fail the boot, which is why
`test_the_orchestrator2_socket_is_still_mounted` exists.

- [ ] **Step 9: Commit**

```bash
git add -A backend
git commit -m "feat(orchestrator2): retire copilot_api and orchestrator_api

The whole agents_orchestrator/orchestrator package: copilot_api (2,722 lines),
orchestrator_api (1,970, reference-only from the start), and copilot_cards /
stage_switch, whose only importers were inside the deleted set.

copilot_api gave a system prompt to three of nine agents and had no `plan` branch at
all, both failing soft — the defect that prompted this rebuild. It stayed alive
through Phases 1-4 deliberately: until Phase 4 it was the only surface that actually
ran agents.

orchestrator2's five references to stage_switch.py are prose explaining why the new
router is built differently. The reasoning is kept and reworded to say the file was
retired here, because a comment pointing at a deleted path is the same defect as one
asserting a guarantee the code does not provide."
```

---

### Task 6: Bound the inbound WebSocket frame (debt #2)

**Files:**
- Modify: `backend/process_api.py:1285`
- Modify: `backend/agents_orchestrator/orchestrator2/ws.py`
- Modify: `docs/local-setup.md`
- Test: `backend/tests/orchestrator2/test_ws_frame_limit.py`

**Interfaces:**
- Produces: `MAX_INBOUND_FRAME_BYTES: int` in `orchestrator2/ws.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_ws_frame_limit.py`:

```python
"""An inbound frame is bounded before it is parsed.

`_remember` bounds what is RETAINED — twenty 1 MB messages no longer means 20 MB held
and re-sent to the routing model. It does nothing about what ARRIVES: a single 20 MB
frame is still received and json-parsed first.

uvicorn's own `ws_max_size` is the real fix and is set for the `python process_api.py`
path, but the CLI path takes a flag this code cannot enforce, so the handler refuses
oversized frames itself. That is the only layer we fully control.
"""
import json

import pytest

from agents_orchestrator.orchestrator2 import ws


def test_the_limit_is_declared_and_sane():
    assert isinstance(ws.MAX_INBOUND_FRAME_BYTES, int)
    # Large enough for a real pasted document, small enough to bound memory.
    assert 64_000 <= ws.MAX_INBOUND_FRAME_BYTES <= 4_000_000


def test_an_oversized_frame_is_refused_without_being_parsed(monkeypatch):
    parsed = []
    real_loads = json.loads

    def _counting_loads(raw, *a, **k):
        parsed.append(len(raw))
        return real_loads(raw, *a, **k)

    monkeypatch.setattr(ws.json, "loads", _counting_loads)
    huge = "x" * (ws.MAX_INBOUND_FRAME_BYTES + 1)
    assert ws._frame_too_large(huge) is True
    assert parsed == [], "an oversized frame must be refused BEFORE json parsing"


def test_a_normal_frame_passes():
    assert ws._frame_too_large('{"type":"user_message","text":"hi","run_id":"r"}') is False


def test_uvicorn_is_configured_with_a_ws_max_size():
    """Covers the `python process_api.py` path. The CLI path needs --ws-max-size,
    which docs/local-setup.md records; neither covers every deployment, which is why
    the handler check above exists as well."""
    import inspect
    import process_api

    src = inspect.getsource(process_api)
    assert "ws_max_size" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_ws_frame_limit.py -q`
Expected: FAIL — `ws.MAX_INBOUND_FRAME_BYTES` does not exist.

- [ ] **Step 3: Implement the handler guard**

In `agents_orchestrator/orchestrator2/ws.py`, near the other module constants:

```python
#: The largest inbound frame this socket will parse. `_remember` bounds what is
#: RETAINED; this bounds what ARRIVES, which is the half that was missing — a single
#: 20 MB frame was received and json-parsed before anything looked at its size.
#:
#: 1 MB is far above any real message (a pasted PRD is a few tens of KB) and far below
#: a frame that could hurt. uvicorn's `ws_max_size` is the real defence and is set for
#: the `python process_api.py` path; the CLI path takes `--ws-max-size`, which this
#: code cannot enforce, so the check below is the layer we always control.
MAX_INBOUND_FRAME_BYTES = 1_000_000


def _frame_too_large(raw: str) -> bool:
    """True when `raw` must be refused unparsed. Measured in BYTES, not characters —
    a message of astral-plane characters is four times its length."""
    return len(raw.encode("utf-8", errors="ignore")) > MAX_INBOUND_FRAME_BYTES
```

In the receive loop, before `json.loads`:

```python
                if _frame_too_large(raw):
                    logger.warning(
                        "orchestrator2 refused an oversized frame (%d bytes) from "
                        "user=%s tenant=%s", len(raw.encode("utf-8", "ignore")),
                        user_id, tenant_id,
                    )
                    await _fail(
                        websocket,
                        "That message is too large to send.",
                        detail=f"limit {MAX_INBOUND_FRAME_BYTES} bytes",
                    )
                    continue
```

`continue`, not `close`: an oversized paste is a user mistake, not an attack, and the
socket staying open lets them send a smaller one. Find the loop with:
```bash
cd backend && grep -n "receive_text\|json.loads" agents_orchestrator/orchestrator2/ws.py
```

- [ ] **Step 4: Set `ws_max_size` for the direct-run path**

`backend/process_api.py:1285`:
```python
    uvicorn.run("process_api:app", host="0.0.0.0", port=80, log_level="info",
                # Bounds an inbound WS frame at the protocol layer, before any
                # handler sees it. orchestrator2/ws.py repeats the check because the
                # CLI path (uv run uvicorn ...) does not execute this line.
                ws_max_size=1_000_000)
```

- [ ] **Step 5: Record the CLI flag**

In `docs/local-setup.md`, beside the uvicorn command, add:

```bash
uv run uvicorn process_api:app --reload --port 8004 --ws-max-size 1000000
```
with a line saying the handler enforces the same bound, so omitting the flag degrades
protection rather than removing it.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd backend && uv run python -m pytest tests/orchestrator2/ -q
```

- [ ] **Step 7: Prove the guard bites**

Raise `MAX_INBOUND_FRAME_BYTES` to `10_000_000`, re-run, confirm
`test_the_limit_is_declared_and_sane` FAILS. Restore. Then change `_frame_too_large` to
`return False`, confirm `test_an_oversized_frame_is_refused_without_being_parsed` FAILS.
Restore in a `finally`, asserting each anchor is unique first.

- [ ] **Step 8: Commit**

```bash
git add backend docs/local-setup.md
git commit -m "fix(orchestrator2): bound the inbound WebSocket frame

_remember bounds what is RETAINED and nothing about what ARRIVES: a 20 MB frame was
received and json-parsed before anything looked at its size. The handler now refuses
an oversized frame unparsed, and uvicorn gets ws_max_size for the direct-run path.

Measured in bytes rather than characters, and the socket stays open — an oversized
paste is a user mistake, not an attack, so they can send a smaller one."
```

---

### Task 7: The boot sweep sees WebSocket routes (debt #4)

**Files:**
- Modify: `backend/shared/authz/dependency.py`
- Test: `backend/tests/test_ws_route_coverage.py`

**Interfaces:**
- Produces: `_WS_IN_HANDLER_AUTH_PATHS: set[str]` in `shared/authz/dependency.py`

**Scope, deliberately.** ~18 WebSocket routes exist across ten agents. Auditing all of
them is separate work; blessing them silently is what happens today. This task makes
the sweep SEE them and requires each to be explicitly recorded, seeding the existing
ones as "authenticates in-handler" — mirroring the `_SIGNALS_IN_BODY_PROTECTED_PATHS`
exception that already exists. What it buys: a NEW socket cannot ship unrecorded.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_ws_route_coverage.py`:

```python
"""Every WebSocket route has made a conscious authz decision.

`assert_all_routes_protected` skipped anything that was not an `APIRoute`, so every
socket was invisible to it. That is how `/projects/[id]/orchestrator` shipped in
Phase 1 with no access check at all: three of four surfaces were gated, the fourth
was not, and no sweep covered it.

WebSocket routes cannot carry `require_permission` — there is no request/response
cycle to hang it on; they authenticate inside the handler, after redeeming a ticket
and before accepting. So the guarantee here is weaker by necessity and still worth
having: every socket is NAMED, and a new one fails the boot until somebody records
which it is.
"""
import pytest

from shared.authz import dependency as dep


def test_websocket_routes_are_enumerated_not_skipped():
    import process_api

    ws_paths = dep.websocket_route_paths(process_api.app)
    assert ws_paths, "the sweep found no WebSocket routes at all — it is still blind"
    assert any("orchestrator2" in p for p in ws_paths), sorted(ws_paths)


def test_every_websocket_route_is_recorded():
    import process_api

    unrecorded = dep.unrecorded_websocket_routes(process_api.app)
    assert unrecorded == [], (
        "these sockets have no recorded authz decision — add each to "
        "_WS_IN_HANDLER_AUTH_PATHS once you have checked what it actually does: "
        + ", ".join(unrecorded)
    )


def test_an_unrecorded_socket_is_reported():
    """The guard itself. Without this the function could return [] unconditionally
    and every test above would still pass."""
    class _Route:
        path = "/sdlc/agent/brand-new/ws"

    assert dep._ws_route_is_recorded(_Route()) is False


def test_the_retired_engines_are_not_in_the_allowlist():
    """Phase 5 deleted them; a lingering entry would quietly re-bless a path if one
    were ever re-added under the same name."""
    for gone in ("/sdlc/agent/copilot/ws", "/sdlc/agent/orchestrator/ws"):
        assert gone not in dep._WS_IN_HANDLER_AUTH_PATHS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_ws_route_coverage.py -q`
Expected: FAIL — `dependency` has no `websocket_route_paths`.

- [ ] **Step 3: Implement**

In `backend/shared/authz/dependency.py`, after `_SIGNALS_IN_BODY_PROTECTED_PATHS`:

```python
# WebSocket routes cannot carry require_permission — there is no request/response
# cycle to attach it to. Each authenticates INSIDE the handler: it redeems a
# single-use ticket and resolves the caller's role before accepting the connection.
#
# This set records that decision per path, so the boot scan can tell "checked, and it
# authenticates in-handler" apart from "nobody has looked". Adding a socket without
# adding it here fails the boot, which is the whole point: before this, sockets were
# invisible to the scan entirely, and that is how a route shipped ungated in Phase 1.
#
# BEING IN THIS SET IS NOT AN AUDIT. These entries record the sockets that existed
# when the scan was extended; confirming each one's in-handler check is real is
# tracked as carried debt, not claimed here.
_WS_IN_HANDLER_AUTH_PATHS: set[str] = set()


def websocket_route_paths(app) -> list[str]:
    """Every WebSocket route path registered on `app`."""
    from starlette.routing import WebSocketRoute

    out = []
    for route in app.routes:
        if isinstance(route, WebSocketRoute) or route.__class__.__name__ == "APIWebSocketRoute":
            out.append(getattr(route, "path", ""))
    return [p for p in out if p]


def _ws_route_is_recorded(route) -> bool:
    return getattr(route, "path", "") in _WS_IN_HANDLER_AUTH_PATHS


def unrecorded_websocket_routes(app) -> list[str]:
    """WebSocket paths with no recorded authz decision."""
    return sorted(p for p in websocket_route_paths(app) if p not in _WS_IN_HANDLER_AUTH_PATHS)
```

Then extend `assert_all_routes_protected` to append unrecorded sockets to `offenders`,
with their own sentence in the raised message so the two failure kinds are not confused.

- [ ] **Step 4: Seed the allowlist from the live app**

```bash
cd backend && uv run python -c "
import process_api
from shared.authz.dependency import websocket_route_paths
for p in sorted(websocket_route_paths(process_api.app)): print(repr(p) + ',')
"
```

Paste the output into `_WS_IN_HANDLER_AUTH_PATHS`. Do this AFTER Task 5, so the
retired engines' sockets are not among them — `test_the_retired_engines_are_not_in_the_allowlist`
enforces that.

- [ ] **Step 5: Run tests and confirm the boot still passes**

```bash
cd backend && uv run python -m pytest tests/test_ws_route_coverage.py tests/orchestrator2/ -q
cd backend && uv run python -c "import process_api; print('import OK')"
cd backend && uv run uvicorn process_api:app --host 127.0.0.1 --port 8004
```
Expected in the log: the D-05 boot scan reports no offenders.

- [ ] **Step 6: Prove the sweep bites**

Remove `"/sdlc/agent/orchestrator2/ws"` from `_WS_IN_HANDLER_AUTH_PATHS`, re-run,
confirm `test_every_websocket_route_is_recorded` FAILS naming it. Restore in a
`finally` after asserting the anchor is unique.

- [ ] **Step 7: Commit**

```bash
git add backend/shared/authz/dependency.py backend/tests/test_ws_route_coverage.py
git commit -m "fix(authz): the boot scan sees WebSocket routes

assert_all_routes_protected skipped anything that was not an APIRoute, so every
socket was invisible to it — which is how /projects/[id]/orchestrator shipped in
Phase 1 with no access check while three sibling surfaces were gated.

Sockets cannot carry require_permission (no request/response cycle), so they
authenticate in-handler. The scan now enumerates them and requires each to be
recorded, seeded with those that existed when it was extended. Being in that set is
NOT an audit and says so: confirming each in-handler check is real stays carried
debt. What this buys is that a NEW socket cannot ship unrecorded."
```

---

# PART 5B — SERVER-BACKED SESSIONS

### Task 8: The Orchestrator persists its turns

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/sessions.py`
- Modify: `backend/agents_orchestrator/orchestrator2/ws.py`
- Test: `backend/tests/orchestrator2/test_session_persistence.py`

**Interfaces:**
- Consumes: `conversation_service.ensure_session_with_id(...)`, `conversation_service.persist_turn(...)`
- Produces:
  - `ORCHESTRATOR_AGENT_KEY = "orchestrator"`
  - `async ensure_session(run_id, *, tenant_id, project_id, user_id, first_message) -> None`
  - `async record_turn(run_id, role, content, *, tenant_id, user_id) -> None`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_session_persistence.py`:

```python
"""The Orchestrator's chat is persisted, so it can be reopened and continued.

Until now the rail was `zustand` + localStorage and said so on screen: "Sessions are
stored in this browser only." Clearing site data lost every chat; another device never
had them; none of it was auditable. Spec §5.3 called for server-backed sessions in
Phase 1 and Phases 1-4 never built them.

The service already exists and every standalone agent uses it. `orchestrator2` only
READ from it (the router pulls history for routing) and never wrote.
"""
import pytest

from agents_orchestrator.orchestrator2 import sessions

_RUN = "11111111-1111-1111-1111-111111111111"
_TENANT = "22222222-2222-2222-2222-222222222222"
_PROJECT = "33333333-3333-3333-3333-333333333333"
_USER = "44444444-4444-4444-4444-444444444444"


def test_the_agent_key_is_the_orchestrator_not_one_of_the_nine():
    """Sessions are listed by agent_id. Reusing one of the nine would mix Orchestrator
    chats into that standalone agent's own history rail."""
    assert sessions.ORCHESTRATOR_AGENT_KEY == "orchestrator"
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS
    assert sessions.ORCHESTRATOR_AGENT_KEY not in AGENT_IDS


@pytest.mark.asyncio
async def test_the_session_id_is_the_run_id(monkeypatch):
    """One conversation is one run (spec §5.3). Keying the session on the run id is
    what makes reopening a chat also reopen its Deliverables, its LangGraph thread and
    its project scope — without a single line of new mapping."""
    captured = {}

    async def _ensure(session_id, tenant_id, **kwargs):
        captured["session_id"] = session_id
        captured.update(kwargs)

    monkeypatch.setattr(sessions.cs, "ensure_session_with_id", _ensure)
    await sessions.ensure_session(
        _RUN, tenant_id=_TENANT, project_id=_PROJECT, user_id=_USER,
        first_message="I need a PRD for billing",
    )
    assert captured["session_id"] == _RUN
    assert captured["run_id"] == _RUN
    assert str(captured["project_id"]) == _PROJECT
    assert captured["created_by"] == _USER


@pytest.mark.asyncio
async def test_the_title_comes_from_the_first_message(monkeypatch):
    """A rail of chats all called "New chat" is a rail you cannot navigate."""
    captured = {}

    async def _ensure(session_id, tenant_id, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sessions.cs, "ensure_session_with_id", _ensure)
    await sessions.ensure_session(
        _RUN, tenant_id=_TENANT, project_id=_PROJECT, user_id=_USER,
        first_message="I need a PRD for the billing rework",
    )
    assert "billing" in (captured.get("title") or "").lower()


@pytest.mark.asyncio
async def test_both_sides_of_a_turn_are_recorded(monkeypatch):
    turns = []

    async def _persist(session_id, role, content, *, tenant_id, author_id=None, **kw):
        turns.append((session_id, role, content, author_id))

    monkeypatch.setattr(sessions.cs, "persist_turn", _persist)
    await sessions.record_turn(_RUN, "user", "hello", tenant_id=_TENANT, user_id=_USER)
    await sessions.record_turn(_RUN, "agent", "hi back", tenant_id=_TENANT, user_id=_USER)
    assert [t[1] for t in turns] == ["user", "agent"]
    assert turns[0][0] == _RUN and turns[0][3] == _USER


@pytest.mark.asyncio
async def test_a_persistence_failure_never_raises(monkeypatch):
    """Losing a transcript row must never cost the user their turn — the same trade
    deliverable capture makes, and the reason `persist_turn` swallows internally."""
    async def _boom(*a, **k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(sessions.cs, "ensure_session_with_id", _boom)
    monkeypatch.setattr(sessions.cs, "persist_turn", _boom)
    await sessions.ensure_session(_RUN, tenant_id=_TENANT, project_id=_PROJECT,
                                  user_id=_USER, first_message="x")
    await sessions.record_turn(_RUN, "user", "x", tenant_id=_TENANT, user_id=_USER)


@pytest.mark.asyncio
async def test_the_socket_records_the_user_turn_and_the_reply(monkeypatch):
    """Through ws.py, so the WIRING is covered and not only the helper.

    Reuses test_ws_routing.py's harness rather than new fakes, so a change to the
    socket's turn loop breaks both files together instead of leaving this one green
    against a shape that no longer exists.
    """
    from agents_orchestrator.orchestrator2 import router as rtr, ws
    from tests.orchestrator2.test_ws_routing import (
        _frame, _no_context, _patch_auth, _record_route, _record_run_agent, _serve,
    )

    recorded = []

    async def _record(run_id, role, content, *, tenant_id, user_id):
        recorded.append((run_id, role, content))

    ensured = []

    async def _ensure(run_id, *, tenant_id, project_id, user_id, first_message):
        ensured.append((run_id, first_message))

    monkeypatch.setattr(ws.sessions, "record_turn", _record)
    monkeypatch.setattr(ws.sessions, "ensure_session", _ensure)

    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="requirements", reason="r", direct_reply=None))
    _record_run_agent(monkeypatch, ws, [
        {"type": "stream_chunk", "content": "here is your PRD"},
        {"type": "stream_end"},
    ])

    await _serve(ws, [_frame(text="I need a PRD")])

    assert ensured and ensured[0][1] == "I need a PRD", (
        "the session is created from the first message, so the rail has a real title"
    )
    assert [(r[1], r[2]) for r in recorded] == [
        ("user", "I need a PRD"),
        ("agent", "here is your PRD"),
    ]
    assert {r[0] for r in recorded} == {_RUN}, "both sides file under the run id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_session_persistence.py -q`
Expected: FAIL — `agents_orchestrator.orchestrator2.sessions` does not exist.

- [ ] **Step 3: Implement `sessions.py`**

```python
"""The Orchestrator's chat, persisted so it can be reopened and continued.

The rail was `zustand` + localStorage and said so on screen: "Sessions are stored in
this browser only." Spec §5.3 called for server-backed sessions in Phase 1; Phases 1-4
never built them, so a chat survived neither a cleared browser nor a change of device,
and nothing about it was auditable.

Almost none of this is new. `shared/services/conversation_service` is complete and
every standalone agent already uses it; `orchestrator2` only READ from it (the router
pulls history for routing) and never wrote. This module is the writer.

SESSION ID == RUN ID. One Orchestrator conversation is one run (spec §5.3), and
`ensure_session_with_id` exists precisely because the Copilot needed the same thing.
Keying on the run id is what makes reopening a chat also reopen its Deliverables, its
LangGraph thread and its project scope, with no mapping table anywhere.

NOTHING HERE MAY FAIL A TURN. `persist_turn` swallows its own errors by design; this
module keeps that property on every path. Losing a transcript row is a smaller harm
than losing the reply — the same trade deliverable capture makes.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from shared.services import conversation_service as cs

logger = logging.getLogger(__name__)

#: The `agent_id` Orchestrator sessions are filed under. NOT one of the nine: sessions
#: are listed per agent, so reusing an agent's id would mix Orchestrator chats into
#: that standalone agent's own history rail.
ORCHESTRATOR_AGENT_KEY = "orchestrator"

#: Longest title derived from a first message. Long enough to tell two chats apart in
#: the rail, short enough not to wrap.
_TITLE_CHARS = 60


def _title_from(first_message: str) -> str:
    """A rail label taken from what the person actually asked.

    A rail of chats all called "New chat" is a rail you cannot navigate.
    """
    text = " ".join((first_message or "").split())
    if not text:
        return "New chat"
    return text[:_TITLE_CHARS].rstrip() + ("…" if len(text) > _TITLE_CHARS else "")


def _as_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


async def ensure_session(
    run_id: str,
    *,
    tenant_id: str,
    project_id: Optional[str],
    user_id: str,
    first_message: str,
) -> None:
    """Idempotently create this run's conversation row. Never raises."""
    try:
        await cs.ensure_session_with_id(
            run_id,
            tenant_id,
            scope_type="agent",
            scope_id=run_id,
            run_id=run_id,
            project_id=_as_uuid(project_id),
            created_by=user_id,
            agent_id=ORCHESTRATOR_AGENT_KEY,
            title=_title_from(first_message),
        )
    except Exception as exc:  # noqa: BLE001 — a lost transcript must not cost a turn
        logger.warning("orchestrator2 could not ensure session %s: %s", run_id, exc)


async def record_turn(
    run_id: str,
    role: str,
    content: str,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Persist one side of a turn. Never raises."""
    try:
        await cs.persist_turn(
            run_id, role, content, tenant_id=tenant_id, author_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrator2 could not persist a %s turn: %s", role, exc)
```

Check `ensure_session_with_id`'s real signature before finalising — if it does not
accept `agent_id`/`title`, extend it (it is the Copilot's helper and the Copilot is
gone, so it has one caller now) rather than working around it here.

- [ ] **Step 4: Wire it into `ws.py`**

Import `sessions`, then in the turn loop: call `sessions.ensure_session(...)` once the
run is verified, `record_turn(run_id, "user", text, ...)` before dispatch, and
`record_turn(run_id, "agent", "".join(reply_text), ...)` beside the existing
`_remember(history, "agent", ...)` call. `_remember` already accumulates exactly the
text the user saw, so persist the same string rather than re-deriving it.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && uv run python -m pytest tests/orchestrator2/ -q
```

- [ ] **Step 6: Prove three tests bite**

Mutate, one at a time, asserting each anchor is unique and probing the function under
test afterwards: (a) `ORCHESTRATOR_AGENT_KEY = "requirements"`, (b) `_title_from`
returning `"New chat"` unconditionally, (c) the `record_turn` call for the agent reply
removed from `ws.py`. Each must kill its own test. Restore in a `finally`.

- [ ] **Step 7: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2 backend/tests/orchestrator2
git commit -m "feat(orchestrator2): persist the chat, so it can be reopened and continued

Spec 5.3 called for server-backed sessions in Phase 1. Phases 1-4 never built them,
so the rail was localStorage and said so on screen: a chat survived neither a cleared
browser nor a change of device, and none of it was auditable.

Almost none of this is new — conversation_service is complete and every standalone
agent uses it; orchestrator2 only read from it and never wrote. session_id == run_id,
the convention ensure_session_with_id exists for, which is what makes reopening a chat
also reopen its Deliverables, its LangGraph thread and its project scope with no
mapping anywhere.

Never raises on any path: losing a transcript row is a smaller harm than losing the
reply, the same trade deliverable capture makes."
```

---

### Task 9: The rail is real history

**Files:**
- Modify: `frontend/components/orchestrator/cockpit.tsx`
- Modify: `frontend/components/orchestrator/session-rail.tsx`
- Test: `frontend/components/orchestrator/__tests__/cockpit-history.test.tsx`

**Interfaces:**
- Consumes: `listConversations(projectId, agentId)`, `getConversationMessages(id)`, `renameConversation(id, title)`, `deleteConversation(id)` from `@/lib/api/conversations`

- [ ] **Step 1: Write the failing test**

`frontend/components/orchestrator/__tests__/cockpit-history.test.tsx`, modelled on
`cockpit-deliverables.test.tsx` (read it first and reuse its mock scaffolding — session,
access scope, projects, models, runs, model-picker, project-picker). Mock
`@/lib/api/conversations` and assert:

```tsx
describe("the Orchestrator rail is server history", () => {
  it("lists the project's saved chats, not this browser's", async () => {
    listConversations.mockResolvedValue([
      { id: "run-1", title: "Billing PRD", agent_id: "orchestrator" },
      { id: "run-2", title: "Auth review", agent_id: "orchestrator" },
    ]);
    renderCockpit();
    expect(await screen.findByText("Billing PRD")).toBeTruthy();
    expect(listConversations).toHaveBeenCalledWith("p1", "orchestrator");
  });

  it("opens a chat and replays its transcript", async () => {
    listConversations.mockResolvedValue([{ id: "run-1", title: "Billing PRD" }]);
    getConversationMessages.mockResolvedValue([
      { id: "m1", seq: 1, role: "user", content: "I need a PRD", content_type: "text" },
      { id: "m2", seq: 2, role: "agent", content: "Here it is", content_type: "text" },
    ]);
    renderCockpit();
    fireEvent.click(await screen.findByText("Billing PRD"));
    expect(await screen.findByText("I need a PRD")).toBeTruthy();
    expect(await screen.findByText("Here it is")).toBeTruthy();
  });

  it("continues an opened chat against its own run", async () => {
    // The whole point: session id IS the run id, so the next turn rejoins the same
    // run — its LangGraph thread, its Deliverables, its project scope.
    listConversations.mockResolvedValue([{ id: "run-1", title: "Billing PRD" }]);
    getConversationMessages.mockResolvedValue([]);
    renderCockpit();
    fireEvent.click(await screen.findByText("Billing PRD"));
    sendMessage("and now the design");
    const turn = sendTurn.mock.calls[0]![0] as { resolveRunId: () => Promise<string> };
    let id = "";
    await act(async () => { id = await turn.resolveRunId(); });
    expect(id).toBe("run-1");
    expect(createRun).not.toHaveBeenCalled();
  });

  it("does not create a run just for clicking New", async () => {
    // A new chat is a draft until its first turn, or every stray click mints a run.
    listConversations.mockResolvedValue([]);
    renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: /new/i }));
    expect(createRun).not.toHaveBeenCalled();
  });

  it("no longer claims sessions live only in this browser", async () => {
    listConversations.mockResolvedValue([]);
    renderCockpit();
    expect(screen.queryByText(/stored in this browser only/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/orchestrator/__tests__/cockpit-history.test.tsx`
Expected: FAIL — the rail renders localStorage sessions and never calls `listConversations`.

- [ ] **Step 3: Implement in `cockpit.tsx`**

Add a query for the project's Orchestrator sessions, and a selection handler that sets
`runIdRef.current` / `setRunId(id)` and replays the transcript into the thread:

```tsx
  const historyQ = useQuery({
    queryKey: ["orchestrator", "sessions", projectId],
    queryFn: () => listConversations(projectId as ProjectId, "orchestrator"),
    enabled: !!projectId,
  });

  // Opening a saved chat IS opening its run: the session id and the run id are the
  // same value (backend `sessions.ensure_session`), so the next turn rejoins this
  // run's LangGraph thread, Deliverables and project scope with no extra wiring.
  const openSession = React.useCallback(async (id: string) => {
    resetSocket();
    runIdRef.current = id;
    setRunId(id);
    setOpenDeliverableId(null);
    const messages = await getConversationMessages(id);
    replaceThread(messages.map(toOrchestratorMessage));
  }, [resetSocket, replaceThread]);
```

The rail's `sessions` prop becomes `historyQ.data` mapped to its shape, plus the local
draft when one exists. `onRename` / `onDelete` call `renameConversation` /
`deleteConversation` and then `historyQ.refetch()`. Refetch after `stream_end` too, so a
chat that has just been created by its first turn appears without a reload.

`replaceThread` and `toOrchestratorMessage` do not exist yet — add them beside the
socket hook's message state, mapping `{role, content}` to `OrchestratorMessage`.

- [ ] **Step 4: Update the rail's footer**

Delete the "Sessions are stored in this browser only." line in `session-rail.tsx:169`.
It stops being true, and a line asserting something untrue is the defect this branch has
corrected nine times.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd frontend && npx vitest run components/orchestrator/ lib/orchestrator/
cd frontend && npm run typecheck && npx eslint components/orchestrator lib/orchestrator
```

- [ ] **Step 6: Prove two tests bite**

Point `openSession` at a fresh run (`runIdRef.current = null`) and confirm
`continues an opened chat against its own run` FAILS; then skip the
`getConversationMessages` replay and confirm `opens a chat and replays its transcript`
FAILS. Restore each in a `finally`.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/orchestrator
git commit -m "feat(orchestrator): the rail is real history, openable and continuable

Lists the project's saved chats from the server instead of this browser, replays a
chat's transcript when opened, and continues it — the session id IS the run id, so the
next turn rejoins that run's LangGraph thread, Deliverables and project scope with no
extra wiring.

A new chat stays a local draft until its first turn, so clicking New repeatedly cannot
litter the database with runs nobody used. The rail no longer says sessions are stored
in this browser only, because that stopped being true."
```

---

### Task 10: Drop the localStorage store

**Files:**
- Modify: `frontend/stores/orchestrator-store.ts`
- Test: `frontend/stores/__tests__/no-local-transcripts.test.ts`

- [ ] **Step 1: Write the failing test**

`frontend/stores/__tests__/no-local-transcripts.test.ts`:

```ts
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * Transcripts live on the server now (Phase 5B). The store keeps only UI state — which
 * chat is selected, and the unsaved draft — so there is no second copy of a
 * conversation to drift from the first.
 *
 * localStorage sessions are NOT migrated. They are per-browser, hold no run id for
 * turns never sent, and cannot be attributed to a user server-side; migrating a shape
 * that can no longer be produced is the mistake ruling R13 already recorded.
 */
describe("the orchestrator store", () => {
  const src = readFileSync("stores/orchestrator-store.ts", "utf8");

  it("does not persist message transcripts", () => {
    expect(src).not.toContain("appendMessage");
    expect(src).not.toContain("patchMessage");
  });

  it("does not persist sessions to localStorage", () => {
    expect(src).not.toContain("persist(");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run stores/__tests__/no-local-transcripts.test.ts`
Expected: FAIL — the store persists and holds `appendMessage`/`patchMessage`.

- [ ] **Step 3: Implement**

Strip `persist(...)`, `appendMessage`, `patchMessage` and the `messages` field from the
store, keeping `activeSessionId`, the draft session and `setModelKey`. Update the module
docstring to say transcripts are server-side and that local sessions are deliberately
not migrated. Then find and fix every consumer:

```bash
cd frontend && grep -rn "appendMessage\|patchMessage\|useOrchestratorStore" --include=*.ts --include=*.tsx . | grep -v node_modules
```

- [ ] **Step 4: Run everything, including a build**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run build
cd frontend && npx eslint components/orchestrator lib/orchestrator stores
```

- [ ] **Step 5: Commit**

```bash
git add frontend/stores frontend/components frontend/lib
git commit -m "refactor(orchestrator): the store keeps UI state, not transcripts

Transcripts are server-side now, so a local copy would be a second version of the
same conversation with nothing keeping the two in step.

localStorage sessions are dropped rather than migrated: they are per-browser, hold no
run id for turns never sent, and cannot be attributed to a user server-side. Migrating
a shape that can no longer be produced is the mistake ruling R13 already recorded."
```

---

## Verification

- [ ] `cd backend && uv run python -m pytest tests/orchestrator2/ -q` — all green
- [ ] `cd backend && uv run python -m pytest tests/ -q` — compare to the recorded baseline of 22 pre-existing failures; anything NEW is a regression
- [ ] `cd frontend && npx vitest run` — all green
- [ ] `cd frontend && npm run typecheck` — clean
- [ ] `cd frontend && npm run build` — **required**; the only check that resolves every route's imports
- [ ] `cd frontend && npx eslint components/orchestrator lib/orchestrator stores` — clean
- [ ] `cd backend && uv run python scripts/live_deliverables_check.py` — 16/16, unchanged
- [ ] `cd backend && uv run python scripts/live_routing_check.py` — 27/29 (`"review this"` and `"fix the tests"` are recorded boundaries; the baseline flips between 27 and 28)
- [ ] Backend boots with `D-05 route-coverage boot scan: ... no offenders` **and** the new WebSocket sweep passing
- [ ] **Live**: Docker up, backend on 8004, frontend on 3000. Sign in as `sarthakk2004@gmail.com` (the only `project_admin`), open the "reall" project's Orchestrator. Ask for a PRD; confirm the Deliverables tab fills. Reload the page; confirm the chat appears in the left rail, opens with its full transcript, and a follow-up turn continues the SAME run (the Deliverables tab still shows the earlier document).
- [ ] Confirm `/projects/<id>/copilot` now 404s and nothing in the UI links to it
- [ ] Whole-branch review by an agent that did not implement these tasks

## Out of scope

BYOK env-fallback audit (debt #5); the e2e rewrite (debt #8); auditing the ~18 existing WebSocket routes the new sweep now lists; RLS inert (debt #1); context truncation (debt #6); `"review this"` routing (debt #7).
