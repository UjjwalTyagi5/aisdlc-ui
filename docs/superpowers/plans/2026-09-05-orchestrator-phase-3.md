# Orchestrator Phase 3 — The Context Agent

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Orchestrator picks the agent itself. "I need a PRD" starts the Requirements agent with no menu, no ordering, and no guessing — and says which agent it picked and why.

**Architecture:** A Context Agent sits in front of the nine delivery agents. It is an LLM holding one tool per agent (spec §5.1, Method A), preceded by a thin deterministic pre-filter for unambiguous commands so the obvious cases cost nothing. It also carries context: before invoking an agent it loads what earlier agents produced on that run and passes it forward. Phase 2's explicit `agent` field becomes an optional override rather than a requirement.

**Tech Stack:** FastAPI, LangGraph, LangChain/ChatLiteLLM, pytest; Next.js 15 + Zod frontend.

**Spec:** `orchestrator_instruction.md` (repo root) — §1.3 (what the Orchestrator is), §5.1 (routing core), §5.4 (context propagation), §11.1 (D10a).

## Global Constraints

- **Nine agents**, ids exactly: `requirements`, `design`, `plan`, `development`, `code_review`, `security`, `testing`, `deployment`, `documentation`.
- `plan` is the **Project Manager agent** in every user-facing string. The id stays `plan`.
- **No positional progression.** No `next_stage()`, no auto-advance, no stage-index arithmetic, no "the next agent is…". Every turn considers all nine; sequence is an outcome of the conversation, never of list order.
- **No gates, no sign-off, no gate language.**
- **Never import from any `*_agent_api.py`** (D10a — those wrappers hold the permission/session machinery the Orchestrator does not inherit).
- **No env-key fallback, ever.** Phase 2 removed it deliberately; a local "it works" on an env key is a lie about production, where no such key exists. Note `copilot_api._classify_switch` DOES have such a fallback — do not copy it.
- BYOK stays project-scoped: the router's own LLM call must use the same resolved model as the turn, which Phase 2 puts on the contextvar.
- Say "Business Unit" in prose; code identifiers keep `workspace`.
- Backend port **8004**; run tests with `uv run python -m pytest` (the bare `uv run pytest` form fails repo-wide on a pre-existing `ModuleNotFoundError: config`).

---

## Verified facts (do not re-derive)

- `agents_orchestrator/orchestrator2/registry.py` exports `AGENT_IDS`, `REGISTRY`, `AgentCapability(agent_id, load_graph, load_prompt, mode)`, `get_capability`, `validate_registry`, and the errors `UnknownAgentError`, `RegistryValidationError`, `PromptNotApplicableError`. All nine resolve a graph; the eight `stream`-mode agents resolve a prompt; `testing` is `invoke` mode and raises `PromptNotApplicableError` by design.
- `agents_orchestrator/orchestrator2/dispatch.py` exports
  `async def run_agent(agent_id, *, text, run_id, tenant_id, project_id, model_id, offering_id) -> AsyncIterator[dict]`.
  It already does `set_run_project(project_id)` → `resolve_model_for_run(..., project_id=project_id)` → `set_resolved_model(resolved)` **before** loading the graph, and yields `agent.selected` first and `stream_end` always.
- `agents_orchestrator/orchestrator2/ws.py` resolves the run from the `runs` row (tenant-scoped) and takes `project_id` from that row, never from the client frame.
- LLM construction pattern (from `dev_agent.py:325-338` and `copilot_api._classify_switch`):
  `resolve_model_for_run(...) -> ResolvedModel(model, litellm_provider, api_key, base_url, alias)`, then
  `ChatLiteLLM(model=…, custom_llm_provider=…, api_base=…, api_key=…, **litellm_key_kwargs(provider, key))`, then
  `llm.bind_tools(tools)`, then `guarded_completion(resolved, bound, messages, tenant_id=…, agent_type=…)`.
- The frontend protocol is `frontend/lib/orchestrator/protocol.ts`. `agent.selected` carries `agent`, `reason`, `run_id`. `tool.call` carries `name` and `status`. An `error` may carry `agent` **only** when the id resolved — an unresolved id makes Zod drop the frame, which has already happened once.
- The old `stage_switch.py` router (rules + LLM fallback) exists in the legacy package. **Reference only.** Its failure was that it fired only on alias matches, so "I need a PRD" did not route, and its progression was positional.

---

## Carried-forward debt this phase closes

| Item | Where it came from |
|---|---|
| `tool.call` is declared in the protocol and consumed by the Activity tab, but `run_agent` never emits it | Phase 2 Task 4, deferred with tracking |
| The socket's Project-Admin check is tenant-scoped standing, not per-project | Phase 2 Task 5, inert while `project_id` was unused — **routing makes it load-bearing** |

---

## File Structure

**Created**
| File | Responsibility |
|---|---|
| `backend/agents_orchestrator/orchestrator2/router.py` | The Context Agent: pre-filter + tool-calling LLM. Decides which agent, or answers directly. |
| `backend/agents_orchestrator/orchestrator2/context.py` | Loads what earlier agents produced on a run and renders the hand-off payload. |
| `backend/tests/orchestrator2/test_router.py` | Router tests with a fake LLM. |
| `backend/tests/orchestrator2/test_context.py` | Context-propagation tests. |

**Modified**
| File | Change |
|---|---|
| `backend/agents_orchestrator/orchestrator2/dispatch.py` | Emit `tool.call`; accept the context payload. |
| `backend/agents_orchestrator/orchestrator2/ws.py` | `agent` becomes optional; route when absent; enforce per-project admin. |
| `frontend/components/orchestrator/cockpit.tsx` | Agent picker becomes an optional override, defaulting to "let the Orchestrator choose". |

---

## Task 1: The deterministic pre-filter

Unambiguous commands should never cost a model call, and their behaviour should be perfectly predictable.

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/router.py`
- Create: `backend/tests/orchestrator2/test_router.py`

**Interfaces:**
- Consumes: `AGENT_IDS` from `registry`.
- Produces: `def prefilter(text: str) -> str | None` — an agent id when the text is an unambiguous imperative naming one, else `None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/orchestrator2/test_router.py
import pytest

from agents_orchestrator.orchestrator2.router import prefilter


@pytest.mark.parametrize(
    "text,expected",
    [
        ("run the testing agent", "testing"),
        ("switch to the security agent", "security"),
        ("use the project manager agent", "plan"),
        ("open the code review agent", "code_review"),
    ],
)
def test_unambiguous_commands_short_circuit(text, expected):
    """These cost no model call and must be perfectly predictable."""
    assert prefilter(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I need a PRD",                      # real intent, no agent named -> the LLM's job
        "document this function",            # mentions docs, but is in-agent work
        "what did the security agent find?", # a question ABOUT an agent, not a request to run it
        "",
    ],
)
def test_ambiguous_text_defers_to_the_model(text):
    """The old router's whole failure was matching aliases anywhere in the text.
    'I need a PRD' names no agent and must reach the LLM; 'document this function'
    names one and must NOT route, because it is a request to the CURRENT agent."""
    assert prefilter(text) is None


def test_the_project_manager_agent_is_never_called_plan_to_users():
    """The id is `plan`; the name is 'Project Manager agent'. Users type the name."""
    assert prefilter("run the project manager agent") == "plan"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_router.py -v`
Expected: FAIL — no `router` module.

- [ ] **Step 3: Implement**

Write `prefilter`. It matches ONLY an explicit imperative naming an agent: a run/switch/use/open-style verb, then an agent name. Anything else returns `None`.

Derive the agent names from `AGENT_IDS` plus a display-name map, so a new agent cannot be missed. **Do not** match a bare alias anywhere in the sentence — that is precisely the old router's defect, and it is why "document this function" wrongly routed.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/router.py backend/tests/orchestrator2/test_router.py
git commit -m "feat(orchestrator2): deterministic pre-filter for unambiguous agent commands"
```

---

## Task 2: The Context Agent's routing decision

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/router.py`
- Modify: `backend/tests/orchestrator2/test_router.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class RoutingDecision:
      agent_id: str | None      # None => answer directly, no delivery agent
      reason: str               # one line, shown to the user
      direct_reply: str | None  # set when agent_id is None

  async def route(text, *, history, run_id, tenant_id, project_id,
                  model_id, offering_id) -> RoutingDecision
  ```

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_prefilter_short_circuits_without_a_model_call(monkeypatch):
    """An unambiguous command must not spend a model call."""
    from agents_orchestrator.orchestrator2 import router

    called = False

    async def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("the model must not be called here")

    monkeypatch.setattr(router, "_ask_model", _boom)
    d = await router.route("run the testing agent", history=[], run_id="r", tenant_id="t",
                           project_id="p", model_id=None, offering_id=None)
    assert d.agent_id == "testing"
    assert called is False


@pytest.mark.asyncio
async def test_intent_without_an_agent_name_reaches_the_model(monkeypatch):
    """'I need a PRD' contains no agent alias. The old router could not route it;
    that limitation is the whole reason this phase exists."""
    from agents_orchestrator.orchestrator2 import router

    async def _fake(*a, **k):
        return router.RoutingDecision(agent_id="requirements", reason="asked for a PRD",
                                      direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", _fake)
    d = await router.route("I need a PRD", history=[], run_id="r", tenant_id="t",
                           project_id="p", model_id=None, offering_id=None)
    assert d.agent_id == "requirements"


@pytest.mark.asyncio
async def test_a_model_naming_an_unknown_agent_is_refused(monkeypatch):
    """The router's tools are generated FROM the registry, so this should be
    impossible — but a model can hallucinate, and a bad id must not reach dispatch."""
    from agents_orchestrator.orchestrator2 import router

    async def _fake(*a, **k):
        return router.RoutingDecision(agent_id="marketing", reason="x", direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", _fake)
    d = await router.route("do the thing", history=[], run_id="r", tenant_id="t",
                           project_id="p", model_id=None, offering_id=None)
    assert d.agent_id is None
    assert d.direct_reply  # says it could not choose, rather than silently doing nothing
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`route()`: try `prefilter` first; on a hit, return immediately with a reason saying it was an explicit command. Otherwise call `_ask_model`.

`_ask_model` builds the LLM exactly as Phase 2's dispatch does — `resolve_model_for_run(..., project_id=project_id)` then `ChatLiteLLM(...)` with `litellm_key_kwargs` — and binds **one tool per agent, generated from `REGISTRY`** so the tool list cannot offer an agent the engine cannot run. Give each tool the agent's display name and a one-line description of what it does.

The system prompt tells it: choose exactly one agent, or answer directly if no delivery work is needed; never invent an agent; sequence is not fixed, any agent may run at any time.

**Validate the model's answer against `AGENT_IDS`.** A hallucinated id becomes `agent_id=None` with a `direct_reply` saying it could not choose — never a silent no-op, never a guess.

**No env-key fallback.** If model resolution fails, let it raise; the socket surfaces it as an `error`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_router.py -v`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(orchestrator2): Context Agent routes by meaning, refusing invented agents"
```

---

## Task 3: Context propagation

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/context.py`
- Create: `backend/tests/orchestrator2/test_context.py`

**Interfaces:**
- Produces: `async def handoff_context(run_id: str, tenant_id: str, target_agent: str) -> str` — a compact markdown summary of what earlier agents produced on this run, or `""` when there is nothing.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/orchestrator2/test_context.py
import pytest


@pytest.mark.asyncio
async def test_empty_run_yields_no_context():
    from agents_orchestrator.orchestrator2.context import handoff_context
    assert await handoff_context("no-such-run", "t", "design") == ""


@pytest.mark.asyncio
async def test_context_is_not_ordered_by_pipeline_position(monkeypatch):
    """Design may run after Development if that is what the conversation asked for.
    Context is 'what exists', never 'what comes before me in a list'."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"development": {"summary": "built X"}, "requirements": {"summary": "R1"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context("r", "t", "design")
    assert "built X" in out and "R1" in out
```

- [ ] **Step 2: Run to verify it fails**, then implement.

`_load_run_artifacts` reads the `Run` row's per-stage artifact columns (`requirements_artifacts`, `design_artifacts`, `plan_artifacts`, … — all nine exist) **tenant-scoped**, exactly as `ws._resolve_run` does. Render a compact summary; cap the length so a long run cannot blow the target agent's context window.

**No ordering by pipeline position.** Include everything that exists, labelled by the agent that produced it.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(orchestrator2): carry earlier agents' output into the next agent's turn"
```

---

## Task 4: Wire routing into the socket, and emit `tool.call`

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/ws.py`, `dispatch.py`
- Modify: `backend/tests/orchestrator2/test_ws_access.py`, `test_dispatch.py`

- [ ] **Step 1: Write the failing tests**

Cover: (a) a message with **no** `agent` routes via the Context Agent rather than erroring; (b) a message **with** `agent` still dispatches that agent directly (an explicit override must win over the router); (c) `agent.selected` carries the router's `reason`; (d) a routing decision of "answer directly" streams the reply and never loads a graph; (e) `tool.call` events reach the socket when the agent invokes a tool.

- [ ] **Step 2: Implement**

In `ws.py`, `agent` becomes optional: absent → `router.route(...)`; present → dispatch it directly. Pass `handoff_context(...)` into `run_agent`.

In `dispatch.py`, emit `tool.call` — `{"type": "tool.call", "name": ..., "status": "running"|"done", "run_id": ...}` — as the graph reports tool activity. This closes the Phase 2 deferral: the event is declared in the protocol and the Activity tab consumes it, so leaving it unimplemented left a declared interface hollow.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(orchestrator2): route by conversation, honour explicit overrides, emit tool.call"
```

---

## Task 5: Per-project admin check, and the frontend override

Routing makes the Phase 2 deferral load-bearing: the socket's Project-Admin check is **tenant-scoped standing**, so a Project Admin of project A could act on project B's run.

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/ws.py`
- Modify: `frontend/components/orchestrator/cockpit.tsx`

- [ ] **Step 1: Write the failing test**

A Project Admin of project A, naming a run belonging to project B **in the same tenant**, must be refused. Tenant scoping alone does not catch this — both runs are in one tenant.

- [ ] **Step 2: Implement the check**, resolving the caller's role **for the run's project**, not their highest standing role.

- [ ] **Step 3: Frontend** — the agent picker becomes an optional override. Default: "Let the Orchestrator choose". Choosing an agent still forces it. The picker must not silently default to an agent.

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(orchestrator): route by default, pick an agent only to override"
```

---

## Phase 3 exit criteria

- [ ] "I need a PRD" starts the Requirements agent with no menu and no agent named.
- [ ] `agent.selected` states the agent and why, every time.
- [ ] An explicit agent choice overrides the router.
- [ ] A hallucinated agent id is refused, visibly — never silently dropped.
- [ ] Unambiguous commands cost no model call.
- [ ] Earlier agents' output reaches the next agent regardless of order.
- [ ] `tool.call` reaches the Activity tab.
- [ ] A Project Admin cannot act on another project's run, even in the same tenant.
- [ ] `uv run python -m pytest tests/orchestrator2/ -v` green; frontend typecheck/lint/vitest green.

**Not in Phase 3:** server-backed sessions, artifact persistence from the new engine, retiring the old engines (Phase 5), the e2e rewrite (deferred by the user in Phase 1).

---

## Self-review

**Spec coverage.** §1.3 (agent starts automatically) → Tasks 1-2, 4; §5.1 Method A + pre-filter → Tasks 1-2; §5.4 context propagation → Task 3; the `tool.call` debt → Task 4; the per-project access debt → Task 5.

**Type consistency.** `RoutingDecision` is defined once in Task 2 and consumed in Task 4. `run_agent`'s signature is Phase 2's, extended with the context payload in Task 4 only.

**Known risk, stated.** The router is an LLM call on every ambiguous turn — latency and cost per message. The pre-filter exists to keep the obvious cases free. If routing proves unreliable in use, the fallback is not a positional sequencer (that was the original defect) but a wider pre-filter plus a clearer tool-description set.
