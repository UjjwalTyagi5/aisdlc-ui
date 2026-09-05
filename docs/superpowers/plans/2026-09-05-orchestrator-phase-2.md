# Orchestrator Phase 2 — Capability Registry and Dispatch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Orchestrator can run **any one of the nine agents, named explicitly**, end to end — real graph, real prompt, real streaming — with a registry that refuses to boot if any agent is missing its graph or its prompt.

**Architecture:** Per D10a the Orchestrator reuses each agent's compiled LangGraph `app` and skips its `*_agent_api.py` wrapper, where the per-role permission and session machinery lives. One registry maps agent id → graph loader, prompt loader, and invocation mode. A startup validator resolves all nine and raises on any gap, in the spirit of the existing `assert_rbac_catalog` guard. A new WebSocket endpoint speaks the protocol pinned in Phase 1.

**Tech Stack:** FastAPI, LangGraph, LangChain messages, asyncpg/SQLAlchemy, pytest; Next.js 15 + Zod on the frontend.

**Spec:** `orchestrator_instruction.md` (repo root) — especially §5.2, §5.5, §11.1 (D10a), §11.2.

## Global Constraints

- **Nine agents**, ids exactly: `requirements`, `design`, `plan`, `development`, `code_review`, `security`, `testing`, `deployment`, `documentation`.
- `plan` is the **Project Manager agent** in every user-facing string. The id stays `plan`.
- **No gates, no sign-off, no gate language** in Orchestrator code or copy.
- **No positional progression**: no `next_stage()`, no auto-advance, no stage-index arithmetic. Phase 2 dispatches only an *explicitly named* agent; routing is Phase 3.
- **Never import from any `*_agent_api.py`.** That is the wrapper D10a exists to avoid.
- The Orchestrator is **Project-Admin-only**, enforced server-side on the socket, not just in the UI.
- Say "Business Unit" in prose; code identifiers keep `workspace`.
- Backend runs on **port 8004** on this machine (`docs/local-setup.md` says 8001 — it is stale).
- Tests run against `sdlc_product_test` via `backend/.env.test`. **Never** point pytest at the app database.

---

## Verified facts (do not re-derive)

Established by reading and executing against the real registry. Every implementer may rely on these.

**All nine graphs exist and export `app`:**

| id | module |
|---|---|
| `requirements` | `agents_orchestrator.requirements_agent.agents.planning` |
| `design` | `agents_orchestrator.design_architecture_agent.agents.architecture` |
| `plan` | `agents_orchestrator.pm_agent.agents.schedule` |
| `development` | `agents_orchestrator.development_agent.agents.dev_agent` |
| `code_review` | `agents_orchestrator.code_review_agent.agents.reviewer` |
| `security` | `agents_orchestrator.security_agent.agents.scanner` |
| `testing` | `agents_orchestrator.testing_agent.agents.testing_agent` (exports `graph_builder`; its `app` is compiled WITHOUT a checkpointer — recompile with one, as `copilot_api._graph_for` does) |
| `deployment` | `agents_orchestrator.deployment_agent.agents.deployer` |
| `documentation` | `agents_orchestrator.documentation_agent.agents.compiler` |

**Known prompt symbols (only three are currently mapped anywhere):**

- `requirements` → `…requirements_agent.agents.planning.INGESTION_SYS_MESSAGE`
- `design` → `…design_architecture_agent.agents.architecture.DESIGN_SYS_MESSAGE`
- `plan` → `…pm_agent.agents.schedule.PM_SYS_MESSAGE`
- `development` → `…development_agent.prompts.dev_agent_prompt.DEV_SYS_MESSAGE`

**The other five prompts must be FOUND, not invented** — see Task 2.

**Two invocation modes.** `copilot_api.STATE_MACHINE_STAGES = {"testing"}`:

- **stream** (eight agents): state `{"messages": [...], "tenant_id", "model_id", "offering_id"}`, run with `graph.astream(state, stream_mode="messages", config)`.
- **invoke** (testing only): state `{"user_prompt": text, "tenant_id", "model_id", "offering_id", …}`, run with `graph.ainvoke(state, config)`, reply read from `state["final_user_message"]`.

Both use `config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}`.

**The bug this phase closes** (spec §11.2): `copilot_api._system_prompt_for()` returns a prompt for only three stages. The other six run with **no instructions**, and its own docstring says such an agent "will churn without producing a useful reply". `_graph_for()` additionally has no `plan` branch. Both fail soft. **Only three of nine agents were ever fully wired.**

---

## File Structure

**Created**
| File | Responsibility |
|---|---|
| `backend/agents_orchestrator/orchestrator2/__init__.py` | Package marker. `orchestrator2` keeps the new engine strictly separate from the legacy `orchestrator/` package until Phase 5 retires it. |
| `backend/agents_orchestrator/orchestrator2/registry.py` | The capability registry: agent id → graph, prompt, mode. Plus `validate_registry()`. |
| `backend/agents_orchestrator/orchestrator2/dispatch.py` | Runs one named agent, yielding protocol events. Owns both invocation modes. |
| `backend/agents_orchestrator/orchestrator2/ws.py` | The Orchestrator WebSocket endpoint. |
| `backend/tests/orchestrator2/test_registry.py` | Registry + validator tests. |
| `backend/tests/orchestrator2/test_dispatch.py` | Dispatch tests with a fake graph. |
| `frontend/app/api/orchestrator/ws-ticket/route.ts` | BFF ticket mint for the new socket. |
| `frontend/lib/orchestrator/use-orchestrator-socket.ts` | The client hook. |

**Modified**
| File | Change |
|---|---|
| `backend/process_api.py` | Mount the new router; call `validate_registry()` at startup. |
| `frontend/components/orchestrator/cockpit.tsx` | Enable the composer; wire the hook. |

---

## Task 1: The registry, with graph resolution for all nine

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/__init__.py` (empty)
- Create: `backend/agents_orchestrator/orchestrator2/registry.py`
- Create: `backend/tests/orchestrator2/__init__.py` (empty), `backend/tests/orchestrator2/test_registry.py`

**Interfaces:**
- Consumes: `config.agent_registry.AGENT_REGISTRY`, `shared.services.orchestrator.progression.STAGE_ORDER`.
- Produces:
  - `AGENT_IDS: tuple[str, ...]` — the nine, in `STAGE_ORDER`
  - `@dataclass(frozen=True) class AgentCapability: agent_id: str; load_graph: Callable[[], Any]; load_prompt: Callable[[], str]; mode: Literal["stream", "invoke"]`
  - `REGISTRY: dict[str, AgentCapability]`
  - `def get_capability(agent_id: str) -> AgentCapability` — raises `UnknownAgentError`
  - `class UnknownAgentError(Exception)`, `class RegistryValidationError(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/orchestrator2/test_registry.py
import pytest

from agents_orchestrator.orchestrator2.registry import (
    AGENT_IDS,
    REGISTRY,
    UnknownAgentError,
    get_capability,
)
from shared.services.orchestrator.progression import STAGE_ORDER


def test_registry_covers_every_stage_in_order():
    """The registry IS the stage list — it cannot silently omit one.

    The old engine's dispatch table omitted `plan`, so the Project Manager agent
    could be routed to but never run, and the failure was swallowed by a
    logger.warning. Deriving the id list from STAGE_ORDER makes that impossible.
    """
    assert list(AGENT_IDS) == list(STAGE_ORDER)
    assert len(AGENT_IDS) == 9
    assert "plan" in AGENT_IDS
    assert set(REGISTRY) == set(AGENT_IDS)


@pytest.mark.parametrize("agent_id", list(STAGE_ORDER))
def test_every_agent_declares_a_mode(agent_id):
    assert REGISTRY[agent_id].mode in ("stream", "invoke")


def test_testing_is_the_only_state_machine_agent():
    """Mirrors copilot_api.STATE_MACHINE_STAGES = {"testing"}."""
    invoke_agents = {a for a, c in REGISTRY.items() if c.mode == "invoke"}
    assert invoke_agents == {"testing"}


def test_unknown_agent_raises_rather_than_returning_none():
    """The old engine returned None for an unmapped stage and carried on, which is
    how six agents ran with no prompt without anyone noticing. Absence must raise."""
    with pytest.raises(UnknownAgentError):
        get_capability("not_an_agent")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator2/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: agents_orchestrator.orchestrator2`.

- [ ] **Step 3: Write the registry**

Create `backend/agents_orchestrator/orchestrator2/registry.py`. Build `REGISTRY` from a table keyed by the nine ids, each with a **lazy** `load_graph` and `load_prompt` (import inside the callable — importing this module must not drag in every agent's heavy dependencies, the same rationale `copilot_api._graph_for` documents). Use the module paths in **Verified facts**. For `testing`, recompile `graph_builder` with a `MemorySaver` and cache it, exactly as `copilot_api._graph_for` does.

For prompts you do not yet know, have `load_prompt` raise a clear `RegistryValidationError` naming the agent — Task 2 fills them in and the validator is what forces it.

Derive `AGENT_IDS` from `STAGE_ORDER`, and assert at import that `set(REGISTRY) == set(STAGE_ORDER)` so a hand-edited table cannot drift from the canonical list.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/orchestrator2/test_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2 backend/tests/orchestrator2
git commit -m "feat(orchestrator2): capability registry derived from STAGE_ORDER"
```

---

## Task 2: Find the five missing prompts, and make absence fatal

This is the task that closes spec §11.2. Six agents currently run with no instructions.

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/registry.py`
- Modify: `backend/tests/orchestrator2/test_registry.py`

**Interfaces:**
- Produces: `def validate_registry() -> None` — resolves graph AND prompt for all nine, raising `RegistryValidationError` listing every failure.

- [ ] **Step 1: Find the five unknown prompts**

Four are known (see Verified facts). Find the system prompt for `code_review`, `security`, `testing`, `deployment`, `documentation`. Each agent has a `prompts/` package — look there first:

```bash
cd backend/agents_orchestrator
ls code_review_agent/prompts security_agent/prompts testing_agent/prompts \
   deployment_agent/prompts documentation_agent/prompts
grep -rnE "^[A-Z_]+ *= *(\"\"\"|f?\")" */prompts/*.py | head -40
```

**Do not invent a prompt.** If an agent genuinely has none, say so in your report and leave its loader raising — a boot failure naming it is the correct outcome, and far better than an agent that churns. Record what you found for each of the five.

- [ ] **Step 2: Write the failing validator test**

```python
def test_validate_registry_resolves_graph_and_prompt_for_all_nine():
    """Six of nine agents used to run with NO system prompt (spec §11.2) — the old
    engine returned None and carried on, so the symptom was an agent that answered
    vaguely rather than an error. This makes that state unshippable."""
    from agents_orchestrator.orchestrator2.registry import validate_registry
    validate_registry()  # raises RegistryValidationError listing any gap


def test_validate_registry_reports_every_gap_not_just_the_first(monkeypatch):
    from agents_orchestrator.orchestrator2 import registry as reg

    def _boom():
        raise ImportError("nope")

    broken = dict(reg.REGISTRY)
    for aid in ("design", "security"):
        broken[aid] = reg.AgentCapability(
            agent_id=aid, load_graph=_boom,
            load_prompt=broken[aid].load_prompt, mode=broken[aid].mode,
        )
    monkeypatch.setattr(reg, "REGISTRY", broken)
    with pytest.raises(reg.RegistryValidationError) as exc:
        reg.validate_registry()
    msg = str(exc.value)
    assert "design" in msg and "security" in msg
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator2/test_registry.py -v`
Expected: FAIL — `validate_registry` does not exist.

- [ ] **Step 4: Implement**

Fill in the five prompt loaders from Step 1. Write `validate_registry()` to call every `load_graph` and `load_prompt`, collect **all** failures, and raise one `RegistryValidationError` naming each agent and what was missing. Collecting all of them matters: failing on the first sends someone round the loop nine times.

A prompt that resolves to an empty or whitespace-only string counts as missing — that is the same silent failure wearing a different hat.

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && uv run pytest tests/orchestrator2/test_registry.py -v`
Expected: PASS, or a clear failure naming exactly which agents lack prompts — report that rather than hiding it.

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/registry.py backend/tests/orchestrator2/test_registry.py
git commit -m "feat(orchestrator2): validate graph and prompt for all nine agents"
```

---

## Task 3: Refuse to boot on a registry gap

**Files:**
- Modify: `backend/process_api.py` (startup path, beside the existing RBAC catalog guard)
- Create: `backend/tests/orchestrator2/test_startup_guard.py`

**Interfaces:**
- Consumes: `validate_registry` (Task 2).

- [ ] **Step 1: Find the existing guard**

```bash
cd backend && grep -rn "assert_rbac_catalog" process_api.py | head
```

That guard runs before anything else and refuses to start on drift. The registry validation goes alongside it, for the same reason: a capability gap must be impossible to ship, not something a user discovers by being ignored.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/orchestrator2/test_startup_guard.py
import inspect


def test_startup_calls_validate_registry():
    """A registry gap must stop the process, exactly as RBAC catalog drift does.
    Without this, a missing agent is discovered by a user getting no answer."""
    import process_api
    src = inspect.getsource(process_api)
    assert "validate_registry" in src, (
        "process_api must call validate_registry() at startup"
    )
    rbac_at = src.find("assert_rbac_catalog")
    reg_at = src.find("validate_registry")
    assert rbac_at != -1 and reg_at != -1
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator2/test_startup_guard.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

Call `validate_registry()` in the same startup path as `assert_rbac_catalog`, letting `RegistryValidationError` propagate so the process refuses to start. Add a one-line comment saying why this is fatal rather than a warning, citing §11.2.

- [ ] **Step 5: Verify it actually guards**

Run the test, then prove the guard bites:

```bash
cd backend && uv run python -c "
from agents_orchestrator.orchestrator2 import registry as r
bad = dict(r.REGISTRY)
aid = 'documentation'
bad[aid] = r.AgentCapability(agent_id=aid, load_graph=lambda: (_ for _ in ()).throw(ImportError('x')), load_prompt=bad[aid].load_prompt, mode=bad[aid].mode)
r.REGISTRY = bad
try:
    r.validate_registry(); print('NOT GUARDED — validate_registry passed with a broken agent')
except r.RegistryValidationError as e:
    print('guarded:', e)
"
```

Expected: prints `guarded: …documentation…`.

- [ ] **Step 6: Commit**

```bash
git add backend/process_api.py backend/tests/orchestrator2/test_startup_guard.py
git commit -m "feat(orchestrator2): refuse to boot when an agent's graph or prompt is missing"
```

---

## Task 4: Dispatch one named agent

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/dispatch.py`
- Create: `backend/tests/orchestrator2/test_dispatch.py`

**Interfaces:**
- Consumes: `get_capability` (Task 1).
- Produces:
  ```python
  async def run_agent(
      agent_id: str, *, text: str, run_id: str, tenant_id: str,
      model_id: str | None, offering_id: str | None,
  ) -> AsyncIterator[dict]:
      """Yields protocol events: agent.selected, stream_chunk, tool.call, error, stream_end."""
  ```

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/orchestrator2/test_dispatch.py
import pytest

from agents_orchestrator.orchestrator2 import dispatch, registry as reg


class _FakeStreamGraph:
    def __init__(self):
        self.seen_state = None
        self.seen_config = None

    async def astream(self, state, stream_mode=None, config=None):
        self.seen_state, self.seen_config = state, config
        for part in ("Hello ", "world"):
            yield (type("M", (), {"content": part})(), {})


@pytest.mark.asyncio
async def test_run_agent_announces_the_agent_before_any_text(monkeypatch):
    """agent.selected must arrive first. The old engine switched agents silently,
    so a wrong choice was invisible until the answer made no sense."""
    fake = _FakeStreamGraph()
    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: fake,
                            load_prompt=lambda: "SYS", mode="stream"),
    )
    events = [e async for e in dispatch.run_agent(
        "design", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None)]
    assert events[0]["type"] == "agent.selected"
    assert events[0]["agent"] == "design"
    assert events[-1]["type"] == "stream_end"
    assert "".join(e.get("content", "") for e in events if e["type"] == "stream_chunk") == "Hello world"


@pytest.mark.asyncio
async def test_run_agent_passes_thread_id_and_system_prompt(monkeypatch):
    fake = _FakeStreamGraph()
    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: fake,
                            load_prompt=lambda: "SYS-PROMPT", mode="stream"),
    )
    _ = [e async for e in dispatch.run_agent(
        "design", text="hi", run_id="run-42", tenant_id="t1",
        model_id=None, offering_id=None)]
    assert fake.seen_config["configurable"]["thread_id"] == "run-42"
    assert any("SYS-PROMPT" in str(getattr(m, "content", m))
               for m in fake.seen_state["messages"])


@pytest.mark.asyncio
async def test_unknown_agent_yields_a_typed_error_not_silence():
    """Fail loudly. The whole point of this phase."""
    events = [e async for e in dispatch.run_agent(
        "nope", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None)]
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["type"] == "stream_end"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator2/test_dispatch.py -v`
Expected: FAIL — no `dispatch` module.

- [ ] **Step 3: Implement**

Write `run_agent`. It must:
1. `get_capability(agent_id)`; on `UnknownAgentError` yield an `error` event then `stream_end` — never raise into the socket, never fall silent.
2. Yield `agent.selected` **first**, with the agent id.
3. Build state by mode: **stream** → `{"messages": [SystemMessage(prompt), HumanMessage(text)], "tenant_id", "model_id", "offering_id"}`; **invoke** → `{"user_prompt": text, "tenant_id", "model_id", "offering_id"}`.
4. `config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}`.
5. **stream**: `async for chunk in graph.astream(state, stream_mode="messages", config=config)` → `stream_chunk` per non-empty `content`. **invoke**: `await graph.ainvoke(state, config=config)` → one `stream_chunk` from `final_user_message`.
6. Wrap the whole run so any exception becomes an `error` event carrying the agent id, then `stream_end`. **Always** yield `stream_end`.

**No `HANDOFF::` sentinel handling** — that was the old engine's auto-advance mechanism and there is no auto-advance here.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/orchestrator2/test_dispatch.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/dispatch.py backend/tests/orchestrator2/test_dispatch.py
git commit -m "feat(orchestrator2): dispatch a named agent, announcing it and failing loudly"
```

---

## Task 5: The Orchestrator WebSocket

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/ws.py`
- Modify: `backend/process_api.py` (mount at `/sdlc/agent/orchestrator2`)
- Create: `backend/tests/orchestrator2/test_ws_access.py`

**Interfaces:**
- Consumes: `run_agent` (Task 4); `config.auth.ws_ticket.redeem_ws_ticket`.
- Produces: `orchestrator2_router` with `@router.websocket("/ws")`.

- [ ] **Step 1: Read the existing socket for the ticket + auth pattern**

```bash
cd backend && sed -n '2400,2460p' agents_orchestrator/orchestrator/copilot_api.py
```

Copy the ticket redemption and connection lifecycle. **Do not** copy gate handling, stage progression, or the handoff sentinel.

- [ ] **Step 2: Write the failing access test**

```python
# backend/tests/orchestrator2/test_ws_access.py
import inspect


def test_socket_enforces_project_admin_server_side():
    """UI gating is not access control. The Phase 1 review found the per-project
    Orchestrator route completely ungated — a URL was enough. The socket must
    check the role itself."""
    from agents_orchestrator.orchestrator2 import ws
    src = inspect.getsource(ws)
    assert "project_admin" in src


def test_socket_has_no_gate_or_progression_machinery():
    from agents_orchestrator.orchestrator2 import ws
    src = inspect.getsource(ws)
    for banned in ("gate.state", "gate.decision", "next_stage", "HANDOFF::", "auto_advance"):
        assert banned not in src, f"{banned} must not appear in the Orchestrator socket"
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator2/test_ws_access.py -v`
Expected: FAIL — no `ws` module.

- [ ] **Step 4: Implement**

The socket: redeem the ticket, resolve the caller's role, **reject non-`project_admin` before accepting any turn**, then per inbound `{"type":"user_message","text","agent","run_id","project_id"}` stream `run_agent(...)`'s events straight out as JSON. Mount it in `process_api.py` at prefix `/sdlc/agent/orchestrator2` beside the existing routers.

Phase 2 requires an explicit `agent` field — routing is Phase 3. If `agent` is absent, emit an `error` saying so. Do **not** default to an agent; a silent default is how the old engine hid its gaps.

- [ ] **Step 5: Verify**

```bash
cd backend && uv run pytest tests/orchestrator2/ -v
uv run uvicorn process_api:app --port 8004   # must boot; registry guard runs
curl -s localhost:8004/openapi.json | grep -c orchestrator2
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/ws.py backend/process_api.py backend/tests/orchestrator2/test_ws_access.py
git commit -m "feat(orchestrator2): Project-Admin-only WebSocket dispatching a named agent"
```

---

## Task 6: Wire the frontend composer

**Files:**
- Create: `frontend/app/api/orchestrator/ws-ticket/route.ts`
- Create: `frontend/lib/orchestrator/use-orchestrator-socket.ts`
- Modify: `frontend/components/orchestrator/cockpit.tsx`

**Interfaces:**
- Consumes: `OrchestratorEvent` from `@/lib/orchestrator/protocol` (Phase 1).

- [ ] **Step 1: Copy the ticket route**

Model on `frontend/app/api/copilot/ws-ticket/route.ts` verbatim, changing only the WS path to `/sdlc/agent/orchestrator2/ws`. The BFF JWT must never reach the browser — mint a short-lived ticket server-side, exactly as that route documents.

- [ ] **Step 2: Write the hook**

`use-orchestrator-socket.ts`: POST the ticket route, open `wsUrl?ticket=…`, and validate **every** inbound frame with `OrchestratorEvent.safeParse`, dropping anything that fails. That validation is why the protocol was pinned in Phase 1 — an unrecognised frame must never reach component state.

Expose `{ messages, send, connState, activeAgent }`. `agent.selected` sets `activeAgent` and appends a visible line naming the agent, so a wrong pick is obvious immediately.

- [ ] **Step 3: Enable the composer**

In `cockpit.tsx`, replace the disabled composer and its "engine arrives in the next phase" copy with the hook. Phase 2 has no router, so the UI must let the user pick which agent to run — an explicit selector, defaulting to nothing. Do **not** default to an agent.

- [ ] **Step 4: Verify live**

Both servers running (backend **8004**), signed in as a Project Admin (`ana@abcbank.com` / `devpassword123`). Pick an agent, send a message, confirm a real streamed reply and that `agent.selected` names it. Then confirm a non-admin cannot reach the socket.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/api/orchestrator frontend/lib/orchestrator/use-orchestrator-socket.ts frontend/components/orchestrator/cockpit.tsx
git commit -m "feat(orchestrator): run a named agent from the Orchestrator composer"
```

---

## Phase 2 exit criteria

- [ ] All nine agents resolve a graph AND a prompt; the process refuses to boot otherwise.
- [ ] `plan` (Project Manager) dispatches — the §2.4 gap closed.
- [ ] The five previously-unmapped prompts are found, or their absence is reported explicitly.
- [ ] A named agent streams a real reply end to end through the Orchestrator UI.
- [ ] `agent.selected` announces every dispatch.
- [ ] Non-`project_admin` is rejected **at the socket**, not just in the UI.
- [ ] No gate, progression, or handoff machinery anywhere in `orchestrator2/`.
- [ ] `uv run pytest tests/orchestrator2/ -v` green; frontend typecheck/lint/vitest green.

**Not in Phase 2:** the Context Agent and automatic routing (Phase 3), server-backed sessions, artifact persistence wiring, retiring the old engines (Phase 5).

---

## Self-review

**Spec coverage.** §5.2 registry + startup validation → Tasks 1–3; §11.2's six-missing-prompts bug → Task 2, the phase's centrepiece; D10a (reuse graphs, skip wrappers) → Task 1's module table and the "never import `*_agent_api.py`" constraint; §5.5 event contract → Task 4 emits it, Task 6 validates it; §1.5 Project-Admin-only → Task 5 enforces it server-side, which the Phase 1 review proved cannot be left to the UI.

**Deliberate gaps.** No routing (Phase 3 — Task 6 therefore needs an explicit agent selector). No artifact persistence: the panel renders from `/runs/{id}/artifacts`, which still works, but nothing new is written yet. Sessions stay browser-local one more phase.

**Type consistency.** `AgentCapability` has one shape across Tasks 1–4. `run_agent`'s signature is fixed in Task 4 and consumed unchanged in Task 5. Event type strings match `frontend/lib/orchestrator/protocol.ts` exactly: `agent.selected`, `stream_chunk`, `tool.call`, `error`, `stream_end`.

**Known unknown, stated as such.** Five prompts are not yet located. Task 2 makes finding them the work, and explicitly forbids inventing one — an agent booting with a plausible-but-wrong prompt would be a worse version of the bug this phase exists to kill.
