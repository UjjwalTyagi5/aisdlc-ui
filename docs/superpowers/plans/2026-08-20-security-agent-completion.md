# Security Agent Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three confirmed, agent-specific gaps between the Security agent's implementation and the PRD (§21.5): no BYOK model support, dropped CWE tags, and an SBOM that doesn't carry the vulnerability counts its own output schema promises.

**Architecture:** Three independent, narrow fixes inside `backend/agents_orchestrator/security_agent/`. No new files except one isolated unit-test file. Each task's test suite is fully independent of the other two tasks' changes.

**Tech Stack:** Python, LangGraph, pytest (`pytest-asyncio`), the existing Trivy/Semgrep/Gitleaks CLI wrappers already installed and verified working this session.

**Spec:** `docs/superpowers/specs/2026-08-20-security-agent-completion-design.md`

## Global Constraints

- No LLM provider key is configured in this environment (BYOK unset; `.env`'s `ANTHROPIC_API_KEY` fallback is dead — confirmed via a direct API call, 401). Every test in this plan must pass **without** a live model call — mock only the model's own response (`_resolve_model`'s return value after Task 1, or `ChatAnthropic` before it), never the tools underneath it.
- Match the exact pattern already used and merged in `backend/agents_orchestrator/code_review_agent/agents/reviewer.py`'s `_resolve_model` (lines 52-89) and `backend/tests/test_code_review_agent_live_e2e.py`'s `_ScriptedModel` class — this plan makes Security consistent with that, not a new pattern.
- Run every test from `backend/` with `uv run python -m pytest <path> -q`. Trivy and Gitleaks are installed via `winget` in this dev environment but **not** on `PATH` in an already-open shell (winget only updates the persistent Windows User `PATH`) — if a test fails with `"status": "unavailable"` instead of real findings, that shell needs
  `export PATH="$PATH:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/Gitleaks.Gitleaks_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/AquaSecurity.Trivy_Microsoft.Winget.Source_8wekyb3d8bbwe"`
  before running pytest, or a fresh terminal opened after the install.
- Every finding-producing tool must keep degrading gracefully (`"status": "unavailable"`/`"error"`, never an exception) when its CLI binary is missing — do not remove or weaken that behavior in any task below.

---

### Task 1: Swappable model key (BYOK) in the Security graph

**Files:**
- Modify: `backend/agents_orchestrator/security_agent/agents/scanner.py:53-80` (current `agent_node`)
- Modify: `backend/tests/test_security_agent_live_e2e.py` (replace the `ChatAnthropic`-mocking helper with a `_resolve_model`-mocking one, matching Code Review's test)
- Test: `backend/tests/test_security_agent_live_e2e.py`, `backend/tests/test_security_agent_tools.py` (new — see Interfaces)

**Interfaces:**
- Produces: `_resolve_model(state: AgentState)` — a module-level function in `scanner.py`, same signature and behavior contract as `code_review_agent/agents/reviewer.py`'s `_resolve_model`: tries `shared.services.model_resolver.resolve_chat_model(model_id=..., offering_id=..., tools=..., system_prompt=...)` first, falls back to a raw `ChatAnthropic(...).bind_tools(tools)` on any exception. Later tasks don't depend on this, but any future test patches this function, not `ChatAnthropic` directly.

- [ ] **Step 1: Read the current `agent_node` to confirm nothing besides model construction needs to move**

Run: view `backend/agents_orchestrator/security_agent/agents/scanner.py` lines 53-80. Confirm it currently reads:

```python
async def agent_node(state: AgentState) -> dict:
    """Invoke the LLM with the current messages + system prompt."""
    from langchain_core.messages import SystemMessage
    from config.env import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

    # Dedup by tool name (native wins) — the model API rejects duplicate names,
    # which happens when two BYO MCP servers expose a like-named tool.
    seen: set = set()
    tools = []
    for t in _tools + get_skill_tools("security") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)

    from langchain_anthropic import ChatAnthropic
    model = ChatAnthropic(
        model=state.get("model_id") or ANTHROPIC_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=8192,
    ).bind_tools(tools)

    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT
    messages = [SystemMessage(content=base + MCP_TOOLS_PROMPT_NOTE)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response]}
```

If the file has drifted from this (e.g. different line numbers), locate the equivalent block by searching for `ChatAnthropic(` inside `agent_node` and proceed with that block instead — the transformation in Step 2 is the same regardless of exact line numbers.

- [ ] **Step 2: Extract `_resolve_model` and have `agent_node` call it**

Replace the block from Step 1 with:

```python
def _resolve_model(state: AgentState):
    """Resolve the LLM model for this invocation — tries the caller's in-app
    BYOK-configured provider first, falls back to the raw .env key on any failure
    (no provider configured, provider disabled, etc). Mirrors
    code_review_agent/agents/reviewer.py's _resolve_model exactly, so Security gains
    BYOK support the same way Code Review already has it."""
    from config.env import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

    model_id = state.get("model_id") or ANTHROPIC_MODEL
    offering_id = state.get("offering_id")

    # Dedup by tool name (native wins) — the model API rejects duplicate names,
    # which happens when two BYO MCP servers expose a like-named tool.
    seen: set = set()
    tools = []
    for t in _tools + get_skill_tools("security") + get_mcp_tools():
        name = getattr(t, "name", None)
        if name in seen:
            continue
        seen.add(name)
        tools.append(t)

    # Per-workspace agent-profile override (contextvar), falls back to the baked prompt.
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT

    try:
        from shared.services.model_resolver import resolve_chat_model
        return resolve_chat_model(
            model_id=model_id,
            offering_id=offering_id,
            tools=tools,
            system_prompt=base,
        )
    except Exception:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=ANTHROPIC_MODEL,
            api_key=ANTHROPIC_API_KEY,
            max_tokens=8192,
        ).bind_tools(tools)


async def agent_node(state: AgentState) -> dict:
    """Invoke the LLM with the current messages + system prompt."""
    from langchain_core.messages import SystemMessage

    model = _resolve_model(state)
    base = get_prompt_override("security") or SECURITY_SYSTEM_PROMPT
    messages = [SystemMessage(content=base + MCP_TOOLS_PROMPT_NOTE)] + list(state["messages"])
    response = await model.ainvoke(messages)
    return {"messages": [response]}
```

Note `base` is computed in both functions (matches Code Review's own `reviewer.py`, which has the same small duplication — `_resolve_model` needs it to pick the system prompt for `resolve_chat_model`'s `system_prompt=` arg, `agent_node` needs it again for the actual `SystemMessage`). Don't try to thread it through as a return value; that changes `_resolve_model`'s signature away from matching Code Review's.

- [ ] **Step 3: Confirm the module still imports cleanly**

Run: `cd backend && uv run python -c "from agents_orchestrator.security_agent.agents import scanner; print(scanner._resolve_model)"`
Expected: prints `<function _resolve_model at 0x...>`, no traceback.

- [ ] **Step 4: Rewrite `test_security_agent_live_e2e.py`'s model-mocking helper**

Open `backend/tests/test_security_agent_live_e2e.py`. Replace the `_mock_chat_anthropic` helper (currently lines 62-71) and its usage (currently lines 144, 146) with the same `_ScriptedModel` pattern `test_code_review_agent_live_e2e.py` already uses:

Replace:
```python
def _mock_chat_anthropic(script: list[AIMessage]):
    """Builds a MagicMock standing in for the ChatAnthropic class, whose instance's
    .bind_tools(...) returns an object whose .ainvoke() pops the next scripted response.
    Everything downstream of this mock (tool dispatch, the tools themselves) is real."""
    bound = MagicMock()
    bound.ainvoke = AsyncMock(side_effect=list(script))
    instance = MagicMock()
    instance.bind_tools.return_value = bound
    cls = MagicMock(return_value=instance)
    return cls, bound
```

with:
```python
class _ScriptedModel:
    """Stands in for the real model. Each .ainvoke() call pops the next canned
    response off the script, so the test controls exactly what the "model" decides
    to do — but the graph, the tool node, and the tools themselves are 100% real
    code, not mocks. Mirrors test_code_review_agent_live_e2e.py's identical helper."""

    def __init__(self, script: list[AIMessage]):
        self._script = list(script)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self._script.pop(0)
```

Then replace the test body's mock-setup and `ainvoke` call (currently):
```python
    mock_cls, bound = _mock_chat_anthropic(script)

    with patch("langchain_anthropic.ChatAnthropic", mock_cls):
        result = await scanner.app.ainvoke(
```
with:
```python
    model = _ScriptedModel(script)

    with patch.object(scanner, "_resolve_model", return_value=model):
        result = await scanner.app.ainvoke(
```

And replace the later assertion:
```python
    # The model was actually driven through all 3 scripted turns.
    assert bound.ainvoke.call_count == 3
```
with:
```python
    # The model was actually driven through all 3 scripted turns.
    assert model.calls == 3
```

Also update the module docstring's paragraph starting "Security's agent_node (agents/scanner.py) builds `ChatAnthropic` inline..." (currently lines 16-19) — that's no longer true after this task. Replace those 4 lines with:
```
Security's agent_node (agents/scanner.py) now resolves its model via _resolve_model
(added in this pass, mirroring Code Review's) -- mocked the same way Code Review's own
live_e2e test mocks it: patch.object(scanner, "_resolve_model", return_value=<a fake
model with a scripted .ainvoke()>).
```

Finally, remove the now-unused `MagicMock` import from the `unittest.mock` import line (keep `AsyncMock` only if still used elsewhere in the file — it is not, after this change, so the import line becomes `from unittest.mock import patch`).

- [ ] **Step 5: Run the updated live_e2e test**

Run: `cd backend && export PATH="$PATH:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/Gitleaks.Gitleaks_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/AquaSecurity.Trivy_Microsoft.Winget.Source_8wekyb3d8bbwe" && uv run python -m pytest tests/test_security_agent_live_e2e.py -q`
Expected: `1 passed`. If it fails on an import error mentioning `MagicMock`, you missed removing a lingering usage — search the file for `MagicMock` and remove any remaining reference.

- [ ] **Step 6: Write a new isolated unit test proving the BYOK-first, fallback-second contract**

Create `backend/tests/test_security_agent_tools.py` (new file — this also hosts Task 2 and Task 3's isolated unit tests, added in their own steps below):

```python
"""Isolated unit coverage for Security agent internals that don't need the full
graph or a live LLM key — model resolution, and (added in later tasks of this same
plan) tool-output parsing details.

Deliberately no module-level `pytestmark = pytest.mark.asyncio` — this file mixes
sync tests (model resolution) with async ones (Task 3's tool calls), and marking
sync `def` tests with the asyncio marker is unnecessary. Async tests below are each
decorated individually with `@pytest.mark.asyncio`.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_resolve_model_tries_byok_first_and_returns_it_on_success():
    from agents_orchestrator.security_agent.agents.scanner import _resolve_model

    fake_byok_model = MagicMock(name="byok_model")
    with patch(
        "shared.services.model_resolver.resolve_chat_model",
        return_value=fake_byok_model,
    ) as mock_resolve:
        result = _resolve_model({"model_id": "claude-x", "offering_id": "off-1"})

    assert result is fake_byok_model
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args.kwargs["model_id"] == "claude-x"
    assert mock_resolve.call_args.kwargs["offering_id"] == "off-1"


def test_resolve_model_falls_back_to_raw_chat_anthropic_when_byok_raises():
    from agents_orchestrator.security_agent.agents.scanner import _resolve_model

    fake_bound_model = MagicMock(name="fallback_model")
    fake_anthropic_instance = MagicMock()
    fake_anthropic_instance.bind_tools.return_value = fake_bound_model

    with patch(
        "shared.services.model_resolver.resolve_chat_model",
        side_effect=RuntimeError("no provider configured"),
    ), patch(
        "langchain_anthropic.ChatAnthropic",
        return_value=fake_anthropic_instance,
    ) as mock_chat_anthropic:
        result = _resolve_model({"model_id": None, "offering_id": None})

    assert result is fake_bound_model
    mock_chat_anthropic.assert_called_once()
```

- [ ] **Step 7: Run the new test file**

Run: `cd backend && uv run python -m pytest tests/test_security_agent_tools.py -q`
Expected: `2 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/agents_orchestrator/security_agent/agents/scanner.py backend/tests/test_security_agent_live_e2e.py backend/tests/test_security_agent_tools.py
git commit -m "feat: give the Security agent BYOK model support, matching Code Review

Extracts _resolve_model (tries the in-app BYOK provider first, falls
back to the raw .env key) instead of agent_node building ChatAnthropic
inline against only the .env key. Behavior is unchanged when no BYOK
provider is configured -- purely additive.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Stop dropping CWE tags from the Semgrep SAST tool

**Files:**
- Modify: `backend/agents_orchestrator/security_agent/tools/semgrep_sast_tool.py:67-77`
- Test: `backend/tests/test_security_agent_tools.py` (appends to the file Task 1 created)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `run_semgrep_sast`'s parsed finding dicts now include a `"cwe"` key (a list, same shape as the existing `"owasp_category"` key) whenever Semgrep's own output includes `extra.metadata.cwe`. No other task depends on this key.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_security_agent_tools.py`:

```python
import json
from unittest.mock import patch


def _fake_semgrep_completed_process(stdout_obj):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(stdout_obj)
    proc.stderr = ""
    return proc


def test_semgrep_sast_tool_preserves_cwe_tags_alongside_owasp():
    from agents_orchestrator.security_agent.tools import semgrep_sast_tool

    raw_semgrep_output = {
        "results": [
            {
                "check_id": "python.lang.security.audit.subprocess-shell-true",
                "path": "vulnerable.py",
                "start": {"line": 4},
                "end": {"line": 4},
                "extra": {
                    "severity": "ERROR",
                    "message": "shell=True is dangerous",
                    "metadata": {
                        "owasp": ["A03:2021"],
                        "cwe": ["CWE-78: OS Command Injection"],
                    },
                },
            }
        ]
    }

    with patch.object(semgrep_sast_tool, "_SEMGREP_BIN", "/fake/semgrep"), patch(
        "pathlib.Path.exists", return_value=True
    ), patch(
        "subprocess.run",
        return_value=_fake_semgrep_completed_process(raw_semgrep_output),
    ):
        result_json = semgrep_sast_tool.run_semgrep_sast.invoke(
            {"target_path": "/fake/target"}
        )

    result = json.loads(result_json)
    assert result["status"] == "ok"
    finding = result["findings"][0]
    assert finding["owasp_category"] == ["A03:2021"]
    assert finding["cwe"] == ["CWE-78: OS Command Injection"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_security_agent_tools.py::test_semgrep_sast_tool_preserves_cwe_tags_alongside_owasp -q`
Expected: `FAIL` — `KeyError: 'cwe'`.

- [ ] **Step 3: Add the missing field**

In `backend/agents_orchestrator/security_agent/tools/semgrep_sast_tool.py`, the finding-building loop currently reads:

```python
        findings = []
        for r in results:
            findings.append({
                "rule_id": r.get("check_id", ""),
                "severity": r.get("extra", {}).get("severity", "WARNING").lower(),
                "message": r.get("extra", {}).get("message", ""),
                "file": r.get("path", ""),
                "line_start": r.get("start", {}).get("line", 0),
                "line_end": r.get("end", {}).get("line", 0),
                "owasp_category": r.get("extra", {}).get("metadata", {}).get("owasp", []),
            })
```

Add one more key, `"cwe"`, on the same pattern as `"owasp_category"`:

```python
        findings = []
        for r in results:
            findings.append({
                "rule_id": r.get("check_id", ""),
                "severity": r.get("extra", {}).get("severity", "WARNING").lower(),
                "message": r.get("extra", {}).get("message", ""),
                "file": r.get("path", ""),
                "line_start": r.get("start", {}).get("line", 0),
                "line_end": r.get("end", {}).get("line", 0),
                "owasp_category": r.get("extra", {}).get("metadata", {}).get("owasp", []),
                "cwe": r.get("extra", {}).get("metadata", {}).get("cwe", []),
            })
```

- [ ] **Step 4: Run the test again to verify it passes**

Run: `cd backend && uv run python -m pytest tests/test_security_agent_tools.py::test_semgrep_sast_tool_preserves_cwe_tags_alongside_owasp -q`
Expected: `PASS`.

- [ ] **Step 5: Run the full new test file to confirm no cross-test breakage**

Run: `cd backend && uv run python -m pytest tests/test_security_agent_tools.py -q`
Expected: `3 passed` (Task 1's two tests plus this one).

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/security_agent/tools/semgrep_sast_tool.py backend/tests/test_security_agent_tools.py
git commit -m "fix: stop dropping CWE tags from the Security agent's Semgrep SAST output

owasp_category was already extracted from Semgrep's rule metadata; cwe
never was, despite the prompt's own output schema documenting both
(\"compliance\":[\"OWASP A03:2021\",\"CWE-89\"]).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: SBOM carries real vulnerability counts, cross-referenced from the same session's Trivy scan

**Files:**
- Modify: `backend/agents_orchestrator/security_agent/config/session_state.py:16-33` (add one field to `ScanSessionState`)
- Modify: `backend/agents_orchestrator/security_agent/tools/security_tools.py:40-49` (`scan_dependencies`) and `:70-97` (`generate_sbom`, `_parse_manifest`)
- Modify: `backend/tests/test_security_agent_live_e2e.py` (split the scripted turns so `scan_dependencies` completes before `generate_sbom` runs — see Step 6 for why)
- Test: `backend/tests/test_security_agent_tools.py` (appends to the file from Tasks 1-2)

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `ScanSessionState.last_trivy_findings: list[dict]` (default `[]`) — the exact list `run_trivy_scan` parses into its `"findings"` key, cached by `scan_dependencies` after a successful (`"status": "ok"`) run. `generate_sbom`'s returned components now include a `"vulnerabilities": <int>` key, cross-referenced against that cached list. No other task depends on these.

- [ ] **Step 1: Write the failing test for the cross-reference logic in isolation**

Append to `backend/tests/test_security_agent_tools.py`:

```python
import pathlib as _pathlib


@pytest.mark.asyncio
async def test_generate_sbom_cross_references_cached_trivy_findings():
    from agents_orchestrator.security_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.security_agent.tools import security_tools
    from config.ws_helper import set_session_id

    session_id = "sbom-cross-ref-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    s.last_trivy_findings = [
        {"cve": "CVE-2018-1000656", "package": "flask", "installed_version": "0.12.2"},
        {"cve": "CVE-2019-1010083", "package": "flask", "installed_version": "0.12.2"},
    ]

    with patch.object(
        security_tools, "_work_dir", return_value=_pathlib.Path("/fake/does/not/matter")
    ), patch.object(
        security_tools.os, "walk", return_value=[("/fake/does/not/matter", [], ["requirements.txt"])]
    ), patch.object(
        security_tools.pathlib.Path, "read_text", return_value="flask==0.12.2\n"
    ):
        result_json = await security_tools.generate_sbom.ainvoke({})

    result = json.loads(result_json)
    flask_component = next(c for c in result["components"] if c["name"] == "flask")
    assert flask_component["vulnerabilities"] == 2

    clear_session(session_id)


@pytest.mark.asyncio
async def test_generate_sbom_reports_zero_vulnerabilities_when_no_trivy_scan_ran_yet():
    from agents_orchestrator.security_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.security_agent.tools import security_tools
    from config.ws_helper import set_session_id

    session_id = "sbom-cross-ref-empty-test"
    clear_session(session_id)
    set_session_id(session_id)
    # No last_trivy_findings set -- get_session() default is [].

    with patch.object(
        security_tools, "_work_dir", return_value=_pathlib.Path("/fake/does/not/matter")
    ), patch.object(
        security_tools.os, "walk", return_value=[("/fake/does/not/matter", [], ["requirements.txt"])]
    ), patch.object(
        security_tools.pathlib.Path, "read_text", return_value="flask==0.12.2\n"
    ):
        result_json = await security_tools.generate_sbom.ainvoke({})

    result = json.loads(result_json)
    flask_component = next(c for c in result["components"] if c["name"] == "flask")
    assert flask_component["vulnerabilities"] == 0

    clear_session(session_id)
```

`pytest` and `json` are already imported earlier in this same file (`json` from Task 2's Step 1, `pytest` needed here — add `import pytest` near the top of the file if Task 2 didn't already add it; Task 2's snippet above only adds `import json` and `from unittest.mock import patch` inline, so add `import pytest` at the top of the file alongside the existing `from unittest.mock import MagicMock, patch` line when implementing this step).

Confirmed: `security_tools.py` does `import pathlib` and `import os` at module level (not `from X import Y`), so `patch.object(security_tools.os, "walk", ...)` and `patch.object(security_tools.pathlib.Path, "read_text", ...)` above are correct as written.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_security_agent_tools.py::test_generate_sbom_cross_references_cached_trivy_findings -q`
Expected: `FAIL` — `KeyError: 'vulnerabilities'`.

- [ ] **Step 3: Add `last_trivy_findings` to the session state**

In `backend/agents_orchestrator/security_agent/config/session_state.py`, the `ScanSessionState` dataclass currently reads (lines 16-33):

```python
@dataclass
class ScanSessionState:
    work_dir: str = ""
    repo_url: str = ""
    pat: str = ""
    project_id: str = ""
    tenant_id: str = ""
    mode: str = ""                       # "branch" | "pr"
    ado_project: str = ""
    repo_name: str = ""
    branch: str = ""
    pr_id: str = ""
    pr_title: str = ""
    head_sha: str = ""
    target_bound: bool = False
    last_artifact: Optional[dict] = None
    system_injected: bool = False
    mcp_tools: list = field(default_factory=list)
    mcp_loaded: bool = False
```

Add one field, next to `last_artifact` (same section — both are "last thing a tool produced"):

```python
    last_artifact: Optional[dict] = None
    last_trivy_findings: list = field(default_factory=list)
```

- [ ] **Step 4: Have `scan_dependencies` cache its findings on the session**

In `backend/agents_orchestrator/security_agent/tools/security_tools.py`, `scan_dependencies` currently reads (lines 40-49):

```python
@tool
async def scan_dependencies() -> str:
    """Run a dependency / vulnerability (SCA) scan on the checked-out repo (Trivy).

    Degrades gracefully if the scanner binary is unavailable. Returns scanner JSON.
    """
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared. Ask the user to select a branch or PR first."
    return await asyncio.to_thread(run_trivy_scan.invoke, {"target_path": str(wd)})
```

Change it to cache the parsed findings onto the session before returning:

```python
@tool
async def scan_dependencies() -> str:
    """Run a dependency / vulnerability (SCA) scan on the checked-out repo (Trivy).

    Degrades gracefully if the scanner binary is unavailable. Returns scanner JSON.
    """
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared. Ask the user to select a branch or PR first."
    result_json = await asyncio.to_thread(run_trivy_scan.invoke, {"target_path": str(wd)})
    try:
        parsed = json.loads(result_json)
        if parsed.get("status") == "ok":
            get_session(get_session_id()).last_trivy_findings = parsed.get("findings", [])
    except (json.JSONDecodeError, AttributeError):
        pass
    return result_json
```

(`json`, `get_session`, and `get_session_id` are all already imported at the top of this file — `import json`, `from agents_orchestrator.security_agent.config.session_state import get_session`, and `from config.ws_helper import broadcast_log, get_session_id` — no new imports needed for this step.)

- [ ] **Step 5: Cross-reference in `generate_sbom`**

In the same file, `generate_sbom` currently reads (lines 70-97):

```python
@tool
async def generate_sbom(max_components: int = 200) -> str:
    """Build a lightweight SBOM by parsing dependency manifests in the repo.

    Returns JSON {components:[{name, version, manifest}], manifests:[...]}. A best-effort
    inventory when a full SBOM scanner isn't available.
    """
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared."
    comps: list[dict] = []
    seen_manifests: list[str] = []
    for dirpath, dirs, files in os.walk(wd):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not _is_manifest(fn):
                continue
            fp = pathlib.Path(dirpath) / fn
            rel = str(fp.relative_to(wd)).replace("\\", "/")
            seen_manifests.append(rel)
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_BYTES]
            except Exception:
                continue
            comps.extend(_parse_manifest(fn, text, rel))
            if len(comps) >= max_components:
                break
    return json.dumps({"components": comps[:max_components], "manifests": seen_manifests})
```

Change the return to cross-reference each component against the session's cached Trivy findings first:

```python
@tool
async def generate_sbom(max_components: int = 200) -> str:
    """Build a lightweight SBOM by parsing dependency manifests in the repo.

    Returns JSON {components:[{name, version, manifest, vulnerabilities}], manifests:[...]}.
    `vulnerabilities` is populated from the same session's scan_dependencies (Trivy) run,
    if it has already run this session -- 0 if it hasn't, never fabricated.
    """
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared."
    comps: list[dict] = []
    seen_manifests: list[str] = []
    for dirpath, dirs, files in os.walk(wd):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not _is_manifest(fn):
                continue
            fp = pathlib.Path(dirpath) / fn
            rel = str(fp.relative_to(wd)).replace("\\", "/")
            seen_manifests.append(rel)
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_BYTES]
            except Exception:
                continue
            comps.extend(_parse_manifest(fn, text, rel))
            if len(comps) >= max_components:
                break

    trivy_findings = get_session(get_session_id()).last_trivy_findings
    for comp in comps:
        comp["vulnerabilities"] = sum(
            1 for f in trivy_findings
            if f.get("package", "").lower() == comp["name"].lower()
            and f.get("installed_version", "") == comp["version"]
        )

    return json.dumps({"components": comps[:max_components], "manifests": seen_manifests})
```

- [ ] **Step 6: Run the two new tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/test_security_agent_tools.py::test_generate_sbom_cross_references_cached_trivy_findings tests/test_security_agent_tools.py::test_generate_sbom_reports_zero_vulnerabilities_when_no_trivy_scan_ran_yet -q`
Expected: `2 passed`.

- [ ] **Step 7: Split the live_e2e test's first turn so `scan_dependencies` completes before `generate_sbom` runs**

LangGraph's `ToolNode` may execute multiple `tool_calls` from a single `AIMessage` concurrently rather than strictly in list order — if `generate_sbom` ran before `scan_dependencies`'s cache write landed, the cross-reference would see an empty `last_trivy_findings` and the live_e2e test would flake between 0 and the real count. Make the ordering explicit instead of relying on incidental sequencing.

In `backend/tests/test_security_agent_live_e2e.py`, the `script` list currently has turn 1 call all four scanners together:

```python
    script = [
        # Turn 1: the "model" runs all four real scanners in one turn (LangGraph's
        # ToolNode executes parallel tool_calls from a single AIMessage).
        AIMessage(
            content="",
            tool_calls=[
                {"name": "scan_dependencies", "args": {}, "id": "call_1"},
                {"name": "scan_code", "args": {}, "id": "call_2"},
                {"name": "scan_secrets", "args": {}, "id": "call_3"},
                {"name": "generate_sbom", "args": {}, "id": "call_4"},
            ],
        ),
```

Split it into two turns — `scan_dependencies` alone first, then the rest (including `generate_sbom`) — so the cache write is guaranteed to have happened before `generate_sbom` reads it:

```python
    script = [
        # Turn 1: scan_dependencies alone, split out from the other three tool calls
        # so its last_trivy_findings cache write is guaranteed to land before
        # generate_sbom (turn 2) reads it -- ToolNode may run same-turn tool_calls
        # concurrently, so this ordering can't be left to chance.
        AIMessage(
            content="",
            tool_calls=[{"name": "scan_dependencies", "args": {}, "id": "call_1"}],
        ),
        # Turn 2: the remaining three scanners, including generate_sbom, which now
        # cross-references turn 1's cached Trivy findings.
        AIMessage(
            content="",
            tool_calls=[
                {"name": "scan_code", "args": {}, "id": "call_2"},
                {"name": "scan_secrets", "args": {}, "id": "call_3"},
                {"name": "generate_sbom", "args": {}, "id": "call_4"},
            ],
        ),
```

The remaining two scripted turns (submit_security_review, then the final no-tool-calls message) are unchanged, just now turns 3 and 4 instead of 2 and 3. Update the two comments that say "Turn 2:" and "Turn 3:" above them to "Turn 3:" and "Turn 4:" respectively so the numbering stays accurate.

Update the assertion `assert model.calls == 3` (from Task 1) to `assert model.calls == 4`, since there are now 4 scripted turns instead of 3.

Add one more assertion after the existing `sbom_out` check, confirming the cross-reference actually worked end-to-end through the real graph (not just the isolated unit tests from Steps 1-6):

```python
    # Real SBOM builder parsed the real requirements.txt.
    sbom_out = _tool_result("generate_sbom")
    flask_component = next(c for c in sbom_out["components"] if c["name"] == "flask" and c["version"] == "0.12.2")
    assert flask_component["vulnerabilities"] >= 1  # cross-referenced from turn 1's real Trivy run
```

(This replaces the existing single-line `assert any(...)` for the SBOM check — the `next(...)` call already confirms presence, so the separate `any(...)` assertion becomes redundant.)

- [ ] **Step 8: Run the full live_e2e test**

Run: `cd backend && export PATH="$PATH:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/Gitleaks.Gitleaks_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/AquaSecurity.Trivy_Microsoft.Winget.Source_8wekyb3d8bbwe" && uv run python -m pytest tests/test_security_agent_live_e2e.py -q`
Expected: `1 passed`.

- [ ] **Step 9: Run the entire Security agent test surface together as a final regression check**

Run: `cd backend && export PATH="$PATH:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/Gitleaks.Gitleaks_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/srk02/AppData/Local/Microsoft/WinGet/Packages/AquaSecurity.Trivy_Microsoft.Winget.Source_8wekyb3d8bbwe" && uv run python -m pytest tests/test_security_agent_live_e2e.py tests/test_security_agent_tools.py tests/test_security_agent_chat_access.py tests/test_security_agent_graph.py tests/test_security_artifact.py tests/test_security_workspace_agent_access.py -q`
Expected: all pass, no regressions in any file this plan didn't touch.

- [ ] **Step 10: Commit**

```bash
git add backend/agents_orchestrator/security_agent/config/session_state.py backend/agents_orchestrator/security_agent/tools/security_tools.py backend/tests/test_security_agent_live_e2e.py backend/tests/test_security_agent_tools.py
git commit -m "feat: cross-reference the Security agent's SBOM against its own Trivy scan

generate_sbom's output schema (per the system prompt) promises a
vulnerabilities count per component; it never actually populated one,
leaving the model to manually correlate two separate tool outputs
itself. scan_dependencies now caches its parsed findings on the
session; generate_sbom cross-references them by package name+version.
0 when scan_dependencies hasn't run yet this session or nothing
matches -- never fabricated.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Final step: update the team tracker

- [ ] **Update `help/portfolio-1-agent-status.md`'s Security section** to mark this completion pass done, listing the three fixes and their tests (mirror how the Code Review section already documents its own live-verification findings). This is documentation, not a code task — no test cycle, just accuracy. Do this after Task 3's commit, in its own commit:

```bash
git add help/portfolio-1-agent-status.md
git commit -m "docs: mark the Security agent completion pass done in the tracker

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
