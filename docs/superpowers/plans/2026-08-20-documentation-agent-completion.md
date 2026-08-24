# Documentation Agent Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Documentation agent the same genuinely-verified status as Code Review and Security — real end-to-end test coverage, an honest Requirements Traceability Matrix (no column overstates its own evidence), and a live frontend tile.

**Architecture:** Three independent, narrow additions. No production code is rewritten except one prompt file — the graph, tools, and WS/REST handlers were read in full this session and confirmed already correct; this plan proves it with tests and closes one real design gap (RTM honesty).

**Tech Stack:** Python, LangGraph, pytest (`pytest-asyncio`), git (already installed and used elsewhere this session), Postgres.

**Spec:** `docs/superpowers/specs/2026-08-20-documentation-agent-completion-design.md`

## Global Constraints

- No LLM provider key is configured in this environment (BYOK unset; `.env`'s `ANTHROPIC_API_KEY` fallback is dead — confirmed via a direct API call, 401, earlier this session). Every test must pass **without** a live model call — mock only the model's own response, never the tools underneath it.
- `Documentation`'s `_resolve_model` (`agents/compiler.py`) already correctly threads `model_id` into both its BYOK-try branch and its `ChatAnthropic` fallback branch — unlike Code Review's and pre-fix Security's, which both dropped it. Do not "fix" this; it isn't broken. Task 2 adds regression tests locking in the existing correct behavior.
- Match the exact live_e2e pattern already used and merged in `backend/tests/test_security_agent_live_e2e.py` and `backend/tests/test_code_review_agent_live_e2e.py`: a `_ScriptedModel` class whose `.ainvoke()` pops one canned response per call, patched over the agent's `_resolve_model` function (never over `ChatAnthropic` directly — Documentation already has a clean `_resolve_model` seam to patch, same as Code Review).
- Every tool must keep degrading gracefully on missing/absent input (no scan binaries apply here, but `open_docs_pr`/SharePoint tools return a clear `"ERROR: ..."` string, never raise, when preconditions aren't met) — do not weaken this in any task.
- Run every test from `backend/` with `uv run python -m pytest <path> -q`. No PATH export is needed for this plan's tests — `generate_changelog` uses `git`, already on PATH in every shell this session (unlike Trivy/Gitleaks, which needed the winget PATH workaround).

---

### Task 1: Live end-to-end test — the real compiled graph, real git repo, real seeded DB row

**Files:**
- Test: `backend/tests/test_documentation_agent_live_e2e.py` (new)

**Interfaces:**
- Consumes: `agents_orchestrator.documentation_agent.agents.compiler.app` (the compiled graph), `agents_orchestrator.documentation_agent.agents.compiler._resolve_model` (the function to patch — signature `_resolve_model(state: AgentState)`, no other args), `agents_orchestrator.documentation_agent.config.session_state.get_session`/`set_session_id` (from `config.ws_helper`), `agents_orchestrator.documentation_agent.tools.doc_tools.inspect_repo`/`generate_changelog`/`read_upstream_artifacts`/`save_document` (called by the graph, not directly by this test — the test only asserts on their `ToolMessage` outputs).
- Produces: nothing later tasks depend on directly — this test is independently runnable evidence, not a shared fixture.

- [ ] **Step 1: Write the git-repo fixture**

Create `backend/tests/test_documentation_agent_live_e2e.py` with this header and fixture:

```python
"""Proves the Documentation agent's actual tool loop end to end, without a live LLM key.

Same technique as tests/test_code_review_agent_live_e2e.py and
tests/test_security_agent_live_e2e.py: no LLM provider is configured (the .env
ANTHROPIC_API_KEY fallback is confirmed dead -- 401), so only the model's own
judgment can't be proven right now. Everything else can: the graph's routing, a real
`git log` parsed by generate_changelog, a real seeded Postgres Run row read by
read_upstream_artifacts, and save_document's real file write -- all driven through the
actual compiled graph with only the model's response scripted.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid as _uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


class _ScriptedModel:
    """Stands in for the real model. Each .ainvoke() call pops the next canned
    response off the script, so the test controls exactly what the "model" decides
    to do -- but the graph, the tool node, and the tools themselves are 100% real
    code, not mocks. Mirrors test_code_review_agent_live_e2e.py's identical helper."""

    def __init__(self, script: list[AIMessage]):
        self._script = list(script)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self._script.pop(0)


def _git(args: list[str], cwd: str) -> None:
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def doc_target_repo():
    """A real git repo with real conventional commits, generated at test time (not
    committed to the codebase -- keeps this test's fixture data out of the repo's own
    history, matching the pattern already used for the other two live_e2e tests)."""
    d = tempfile.mkdtemp(prefix="doc_agent_live_e2e_")
    _git(["init", "-q"], d)
    _git(["config", "user.email", "test@test.com"], d)
    _git(["config", "user.name", "Test"], d)
    (Path(d) / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(["add", "README.md"], d)
    _git(["commit", "-q", "-m", "chore: initial commit"], d)
    (Path(d) / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(["add", "app.py"], d)
    _git(["commit", "-q", "-m", "feat: add hello world entrypoint"], d)
    (Path(d) / "app.py").write_text("print('hi')\nprint('fixed')\n", encoding="utf-8")
    _git(["add", "app.py"], d)
    _git(["commit", "-q", "-m", "fix: correct greeting output"], d)
    yield d
    shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 2: Write the seeded-DB fixture**

Append (this mirrors the org/workspace/project/run seeding pattern already used in
`test_security_agent_chat_access.py` and `test_run_active_agents_access.py` this
session):

```python
@pytest.fixture
async def seeded_run():
    """A real org/workspace/project/run with two upstream artifacts populated
    (requirements, security) and one deliberately left null (design) -- proves
    read_upstream_artifacts's dual behavior (real data vs. null) against a real query,
    not a mock."""
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    run_id = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :sl, 'Doc Agent Test')"
        ), {"i": org, "sl": f"doc-agent-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, track) "
            "VALUES (:i, :w, :t, 'Doc Agent Project', 'greenfield')"
        ), {"i": project, "w": unit, "t": org})
        await s.execute(text(
            "INSERT INTO runs (id, project_id, tenant_id, status, stage, current_stage, "
            "gate_pending, requirements_payload, security_artifacts, created_at, updated_at) "
            "VALUES (:r, :p, :t, 'running', 'documentation', 'documentation', false, "
            "CAST(:req AS jsonb), CAST(:sec AS jsonb), now(), now())"
        ), {
            "r": run_id, "p": project, "t": org,
            "req": json.dumps({"summary": "Users can log in.", "stories": [{"id": "US-1", "title": "Login"}]}),
            "sec": json.dumps({"risk_score": "low", "signoff": {"decision": "pass"}, "findings": []}),
        })
    yield {"org": org, "project": project}
```

- [ ] **Step 3: Write the live_e2e test itself**

Append:

```python
@pytest.mark.asyncio
async def test_the_real_tool_loop_reads_git_history_and_upstream_artifacts_and_saves_a_doc(
    doc_target_repo, seeded_run,
):
    from agents_orchestrator.documentation_agent.agents import compiler
    from agents_orchestrator.documentation_agent.config.session_state import get_session, clear_session
    from config.ws_helper import set_session_id

    session_id = "doc-live-e2e-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    s.work_dir = doc_target_repo
    s.repo_name = "test-repo"
    s.source_branch = "main"
    s.tenant_id = seeded_run["org"]
    s.project_id = seeded_run["project"]

    script = [
        # Turn 1: the "model" inspects the repo and reads upstream artifacts -- two
        # independent tool calls in one turn (proves the graph/ToolNode handles a
        # multi-call turn correctly, same check already made for Security's live_e2e).
        AIMessage(
            content="",
            tool_calls=[
                {"name": "inspect_repo", "args": {}, "id": "call_1"},
                {"name": "read_upstream_artifacts", "args": {}, "id": "call_2"},
            ],
        ),
        # Turn 2: the "model" pulls the real changelog.
        AIMessage(
            content="",
            tool_calls=[{"name": "generate_changelog", "args": {"since_ref": ""}, "id": "call_3"}],
        ),
        # Turn 3: the "model" saves a document built from what it actually saw --
        # exercises save_document's real file write and session-state update.
        AIMessage(
            content="",
            tool_calls=[{
                "name": "save_document",
                "args": {
                    "doc_type": "changelog",
                    "title": "Changelog",
                    "filename": "CHANGELOG.md",
                    "markdown_contents": "### Added\n- feat: add hello world entrypoint\n\n### Fixed\n- fix: correct greeting output\n",
                },
                "id": "call_4",
            }],
        ),
        # Turn 4: no more tool calls -- route_fn sends this straight to END.
        AIMessage(content="Changelog generated and saved."),
    ]
    model = _ScriptedModel(script)

    with patch.object(compiler, "_resolve_model", return_value=model):
        result = await compiler.app.ainvoke(
            {
                "messages": [HumanMessage(content="Generate the changelog and save it.")],
                "tenant_id": seeded_run["org"],
                "model_id": None,
                "offering_id": None,
            },
            config={"configurable": {"thread_id": session_id}},
        )

    # The model was actually driven through all 4 scripted turns.
    assert model.calls == 4

    def _tool_result(name: str) -> str:
        msgs = [m for m in result["messages"] if getattr(m, "name", None) == name]
        assert len(msgs) == 1, f"expected exactly one {name} ToolMessage, got {len(msgs)}"
        return msgs[0].content

    # Real inspect_repo detected the real repo's real language and README.
    inspect_out = json.loads(_tool_result("inspect_repo"))
    assert inspect_out["languages"] == ["Python"]
    assert "README.md" in inspect_out["notable_files"]["readme"]

    # Real read_upstream_artifacts returned the real seeded requirements + security
    # rows, and correctly null'd design (never populated for this run).
    upstream_out = json.loads(_tool_result("read_upstream_artifacts"))
    assert upstream_out["requirements"]["stories"][0]["id"] == "US-1"
    assert upstream_out["security"]["signoff"]["decision"] == "pass"
    assert upstream_out["design"] is None

    # Real generate_changelog parsed the real git log and grouped it correctly.
    changelog_out = json.loads(_tool_result("generate_changelog"))
    assert changelog_out["status"] == "ok"
    assert changelog_out["commit_count"] == 3
    assert "feat: add hello world entrypoint" in changelog_out["changelog"]
    assert "### Added" in changelog_out["changelog"]
    assert "### Fixed" in changelog_out["changelog"]

    # Real save_document wrote a real file and updated real session state.
    assert len(s.generated_docs) == 1
    assert s.generated_docs[0]["filename"] == "CHANGELOG.md"
    assert Path(s.generated_docs[0]["path"]).exists()
    assert "feat: add hello world entrypoint" in Path(s.generated_docs[0]["path"]).read_text(encoding="utf-8")

    clear_session(session_id)
```

- [ ] **Step 4: Run the test**

Run: `cd backend && uv run python -m pytest tests/test_documentation_agent_live_e2e.py -q`
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_documentation_agent_live_e2e.py
git commit -m "test: prove the Documentation agent's real tool loop end to end

Drives the actual compiled graph with only the model's response
scripted -- every tool call underneath is real: a real git repo's
history through generate_changelog, a real seeded Postgres Run row
through read_upstream_artifacts (including its null-column case), and
a real file write through save_document.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Isolated unit tests — tool edge cases and the BYOK regression guard

**Files:**
- Test: `backend/tests/test_documentation_agent_tools.py` (new)

**Interfaces:**
- Consumes: `agents_orchestrator.documentation_agent.tools.doc_tools` (all tools, called via `.ainvoke({...})` since they're all `async def`), `agents_orchestrator.documentation_agent.agents.compiler._resolve_model`.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests — `generate_changelog` edge cases**

Create `backend/tests/test_documentation_agent_tools.py`:

```python
"""Isolated unit coverage for Documentation agent internals that don't need the full
graph or a live LLM key -- tool-level edge cases and model-resolution regression
guards. The full-loop proof lives in test_documentation_agent_live_e2e.py; this file
is deliberately narrower and faster.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_orchestrator.documentation_agent.config.session_state import get_session, clear_session
from agents_orchestrator.documentation_agent.tools import doc_tools
from config.ws_helper import set_session_id


def _git(args: list[str], cwd: str) -> None:
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def bound_session():
    """A session bound to a fresh temp work_dir, cleared before and after."""
    session_id = "doc-tools-unit-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    d = tempfile.mkdtemp(prefix="doc_agent_tools_test_")
    s.work_dir = d
    yield s, d
    clear_session(session_id)
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_generate_changelog_defaults_an_unconventional_prefix_to_changed(bound_session):
    s, d = bound_session
    _git(["init", "-q"], d)
    _git(["config", "user.email", "t@t.com"], d)
    _git(["config", "user.name", "T"], d)
    (Path(d) / "a.txt").write_text("x", encoding="utf-8")
    _git(["add", "a.txt"], d)
    _git(["commit", "-q", "-m", "bump dependency version"], d)  # no conventional prefix

    result = json.loads(await doc_tools.generate_changelog.ainvoke({"since_ref": ""}))

    assert result["status"] == "ok"
    assert "### Changed" in result["changelog"]
    assert "bump dependency version" in result["changelog"]


@pytest.mark.asyncio
async def test_generate_changelog_excludes_merge_commits(bound_session):
    s, d = bound_session
    # -b trunk: explicit initial branch name -- git's default-branch-name config
    # (master vs. main) varies per machine, so this can't be left implicit if the
    # test is going to `checkout` back to it by name.
    _git(["init", "-q", "-b", "trunk"], d)
    _git(["config", "user.email", "t@t.com"], d)
    _git(["config", "user.name", "T"], d)
    (Path(d) / "a.txt").write_text("x", encoding="utf-8")
    _git(["add", "a.txt"], d)
    _git(["commit", "-q", "-m", "feat: base commit"], d)
    _git(["checkout", "-q", "-b", "side"], d)
    (Path(d) / "b.txt").write_text("y", encoding="utf-8")
    _git(["add", "b.txt"], d)
    _git(["commit", "-q", "-m", "feat: side commit"], d)
    _git(["checkout", "-q", "trunk"], d)
    _git(["merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side"], d)

    result = json.loads(await doc_tools.generate_changelog.ainvoke({"since_ref": ""}))

    assert result["status"] == "ok"
    assert "Merge branch" not in result["changelog"]
    assert result["commit_count"] == 2  # base + side, not the merge commit


@pytest.mark.asyncio
async def test_generate_changelog_with_no_commits_yet_returns_cleanly(bound_session):
    s, d = bound_session
    _git(["init", "-q"], d)
    # No commits at all.

    result = json.loads(await doc_tools.generate_changelog.ainvoke({"since_ref": ""}))

    assert result["status"] == "ok"
    assert result["commit_count"] == 0
    assert result["changelog"] == ""
```

- [ ] **Step 2: Run to verify they fail for the right reason, then confirm they pass**

Run: `cd backend && uv run python -m pytest tests/test_documentation_agent_tools.py -q`

These three should already pass against the existing `generate_changelog` implementation
(this step is proving the *tests* are correct, not fixing production code — unlike a
red/green TDD cycle for new behavior, `generate_changelog`'s bucketing and `--no-merges`
flag already exist). Expected: `3 passed`. If any fails, read the failure carefully: it
means the test's assumption about existing behavior is wrong, not that the tool has a
bug to fix — re-check the tool's actual current behavior (`doc_tools.py`'s
`generate_changelog`) before changing anything.

- [ ] **Step 3: Add `read_upstream_artifacts`'s all-null case**

Append:

```python
@pytest.mark.asyncio
async def test_read_upstream_artifacts_returns_all_null_with_no_tenant_bound():
    session_id = "doc-tools-unbound-test"
    clear_session(session_id)
    set_session_id(session_id)
    # s.tenant_id / s.project_id left at their "" defaults -- never bound.

    result = json.loads(await doc_tools.read_upstream_artifacts.ainvoke({}))

    assert result == {
        "requirements": None, "design": None, "development": None,
        "testing": None, "code_review": None, "security": None,
    }
    clear_session(session_id)
```

- [ ] **Step 4: Add `save_document`'s sanitization, dedup, and empty-content guard**

Append:

```python
@pytest.mark.asyncio
async def test_save_document_sanitizes_a_non_kebab_filename(bound_session, monkeypatch):
    s, d = bound_session
    s.project_id = "proj-1"
    out_root = tempfile.mkdtemp(prefix="doc_agent_output_")
    monkeypatch.setattr(doc_tools, "DOCS_OUTPUT_ROOT", out_root)

    result = await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Overview",
        "filename": "My Overview!!.docx", "markdown_contents": "# Overview\n",
    })

    assert "Saved" in result
    assert len(s.generated_docs) == 1
    saved_name = s.generated_docs[0]["filename"]
    assert saved_name.endswith(".md")  # non-.md extension coerced, not left as .docx
    assert " " not in saved_name and "!" not in saved_name
    shutil.rmtree(out_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_save_document_replaces_rather_than_duplicates_on_the_same_filename(bound_session, monkeypatch):
    s, d = bound_session
    s.project_id = "proj-1"
    out_root = tempfile.mkdtemp(prefix="doc_agent_output_")
    monkeypatch.setattr(doc_tools, "DOCS_OUTPUT_ROOT", out_root)

    await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Overview v1",
        "filename": "overview.md", "markdown_contents": "# v1\n",
    })
    await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Overview v2",
        "filename": "overview.md", "markdown_contents": "# v2\n",
    })

    assert len(s.generated_docs) == 1  # replaced, not appended
    assert s.generated_docs[0]["title"] == "Overview v2"
    shutil.rmtree(out_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_save_document_refuses_empty_content(bound_session):
    s, d = bound_session
    result = await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Empty", "filename": "empty.md",
        "markdown_contents": "   ",
    })
    assert result.startswith("ERROR")
    assert len(s.generated_docs) == 0
```

(`DOCS_OUTPUT_ROOT` is imported at module level in `doc_tools.py` as
`from config.env import DOCS_OUTPUT_ROOT` — `monkeypatch.setattr(doc_tools, "DOCS_OUTPUT_ROOT", out_root)`
overrides the name inside `doc_tools`'s own namespace, which is what `_output_dir` actually reads.)

- [ ] **Step 5: Add `open_docs_pr`'s precondition checks (mocked, no real ADO call)**

Append:

```python
@pytest.mark.asyncio
async def test_open_docs_pr_refuses_with_no_documents_generated(bound_session):
    s, d = bound_session
    result = await doc_tools.open_docs_pr.ainvoke({})
    assert result.startswith("ERROR")
    assert "no documents generated" in result.lower()


@pytest.mark.asyncio
async def test_open_docs_pr_refuses_with_no_prepared_target(bound_session):
    s, d = bound_session
    s.generated_docs = [{"filename": "x.md", "contents": "x", "id": "1", "type": "overview", "title": "X", "format": "md", "path": "", "bytes": 1}]
    s.repo_name = ""  # never prepared
    result = await doc_tools.open_docs_pr.ainvoke({})
    assert result.startswith("ERROR")
    assert "no prepared target" in result.lower()
```

- [ ] **Step 6: Add the SharePoint tools' graceful "not connected" degradation (mocked)**

Append:

```python
@pytest.mark.asyncio
async def test_publish_to_sharepoint_reports_not_connected_cleanly(bound_session):
    s, d = bound_session
    s.tenant_id = "tenant-1"
    s.generated_docs = [{"filename": "x.md", "contents": "x", "id": "1", "type": "overview", "title": "X", "format": "md", "path": "", "bytes": 1}]

    with patch(
        "shared.services.notification_targets.sharepoint_target",
        new=AsyncMock(return_value=None),
    ):
        result = await doc_tools.publish_to_sharepoint.ainvoke({})

    assert result.startswith("ERROR")
    assert "not connected" in result.lower()


@pytest.mark.asyncio
async def test_list_sharepoint_documents_reports_not_connected_cleanly(bound_session):
    s, d = bound_session
    s.tenant_id = "tenant-1"

    with patch(
        "shared.services.notification_targets.sharepoint_target",
        new=AsyncMock(return_value=None),
    ):
        result = await doc_tools.list_sharepoint_documents.ainvoke({})

    assert result.startswith("ERROR")
    assert "not connected" in result.lower()
```

- [ ] **Step 7: Add the two `_resolve_model` regression tests (mirrors Security's exact pair)**

Append:

```python
def test_resolve_model_tries_byok_first_and_returns_it_on_success():
    from agents_orchestrator.documentation_agent.agents.compiler import _resolve_model

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


def test_resolve_model_falls_back_to_raw_chat_anthropic_preserving_model_id():
    from agents_orchestrator.documentation_agent.agents.compiler import _resolve_model

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
        result = _resolve_model({"model_id": "claude-requested", "offering_id": None})

    assert result is fake_bound_model
    # THE regression this test guards: compiler.py's fallback already uses
    # state.get("model_id") or ANTHROPIC_MODEL correctly -- unlike Code Review's and
    # pre-fix Security's, which both hardcoded the default here. Do not let this drift.
    assert mock_chat_anthropic.call_args.kwargs["model"] == "claude-requested"
```

- [ ] **Step 8: Run the full file**

Run: `cd backend && uv run python -m pytest tests/test_documentation_agent_tools.py -q`
Expected: `13 passed` (3 changelog + 1 upstream-artifacts + 3 save_document + 2 open_docs_pr + 2 SharePoint + 2 resolve_model). If your actual count differs, that means either a step above was skipped/duplicated or a test itself expanded into more than one case — trust what you actually wrote over this number, but double-check you didn't drop a step if they disagree.

- [ ] **Step 9: Commit**

```bash
git add backend/tests/test_documentation_agent_tools.py
git commit -m "test: isolated unit coverage for Documentation agent tools

generate_changelog edge cases (unconventional prefix, merge exclusion,
no-commits), read_upstream_artifacts's all-null case, save_document's
sanitization/dedup/empty-content guards, open_docs_pr and SharePoint
tools' precondition checks (mocked -- no live external creds in this
environment), and two _resolve_model regression tests locking in the
model_id-preserving fallback this agent already gets right.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: RTM honesty fix, `builtAgents` flip, and tracker update

**Files:**
- Modify: `backend/agents_orchestrator/documentation_agent/prompts/doc_prompt.py`
- Modify: `frontend/app/(app)/projects/[id]/page.tsx`
- Modify: `help/portfolio-1-agent-status.md`

**Interfaces:**
- Consumes: Tasks 1-2's passing test suites (this task's Step 4 re-runs them as a regression check after the prompt edit).
- Produces: nothing later — this is the plan's final task.

- [ ] **Step 1: Fix the RTM deliverable description**

In `backend/agents_orchestrator/documentation_agent/prompts/doc_prompt.py`, the `rtm`
bullet currently reads (inside the `## Deliverables you can produce` section):

```
- **rtm**: Requirements Traceability Matrix (requirement → design → code → test → finding). Build it fully from upstream artifacts when present; otherwise produce a repo-derived skeleton and clearly mark unknown columns "N/A (no upstream artifact)".
```

Replace it with:

```
- **rtm**: Requirements Traceability Matrix (requirement → design → code → test → finding).
  Only two columns are ever structurally verifiable from real IDs: Requirement (from
  read_upstream_artifacts's requirements.stories[].id / .acceptance_criteria[].id) and
  Code Review (from its requirements_coverage[].ac_id, when present). The Design,
  Development, Testing, and Security columns have NO requirement-ID field in their
  artifacts to match against — never present a match in one of these columns with the
  same confidence as the two verified columns. For each of those four columns: write
  "N/A (no upstream artifact)" when nothing exists to look at; if you find a plausible
  textual correlation (e.g. a story title echoed in a design doc's prose), write it
  prefixed exactly "Inferred — not structurally traceable, verify manually: " followed
  by your finding. Never omit that prefix for a non-Requirement/Code-Review column.
```

- [ ] **Step 2: Verify the prompt file still imports/loads correctly**

Run: `cd backend && uv run python -c "from agents_orchestrator.documentation_agent.prompts.doc_prompt import DOC_SYSTEM_PROMPT; assert 'Inferred' in DOC_SYSTEM_PROMPT; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Flip `builtAgents`**

In `frontend/app/(app)/projects/[id]/page.tsx`, find the line (around line 122):

```typescript
    const builtAgents: Phase[] = ["security"];
```

Change to:

```typescript
    const builtAgents: Phase[] = ["security", "documentation"];
```

- [ ] **Step 4: Run the full backend Documentation + Security test surface as a regression check**

Run: `cd backend && uv run python -m pytest tests/test_documentation_agent_live_e2e.py tests/test_documentation_agent_tools.py tests/test_documentation_agent_chat_access.py tests/test_security_agent_live_e2e.py tests/test_security_agent_tools.py -q`
Expected: all pass, zero regressions. (Security's suite is included here only as a
sanity check that this task's prompt-file edit — a different file — didn't somehow
break an unrelated import chain; it should be untouched.)

- [ ] **Step 5: Verify the frontend still typechecks**

Run: `cd frontend && npx tsc --noEmit`
This is a one-line addition to an existing, already-typed `Phase[]` array — it should
introduce zero new errors. If the run reports errors, check specifically whether any of
them reference the `builtAgents` line or `"documentation"` not being assignable to
`Phase` (check `frontend/lib/agents.ts` for the `Phase` type definition in that case —
the edit itself would be wrong, not the type system). Pre-existing, unrelated errors
elsewhere in the codebase (if the command already reported any before this change) are
not this task's responsibility to fix — only confirm this specific line didn't add a
new one.

- [ ] **Step 6: Update the tracker**

In `help/portfolio-1-agent-status.md`, find the Documentation section (agent #8) and
add a "Real-logic verification" subsection matching the style already used for Code
Review's and Security's sections (see those sections for the exact format — a
"**Completion pass (2026-08-20)**" block listing what was found/fixed/tested). Cover:
the live_e2e test proving the graph/tools work with real git history and a real
seeded DB row; the isolated unit tests; the RTM structural-traceability finding (only
Requirements and Code Review columns have real IDs) and the prompt fix; and that
`builtAgents` now includes `"documentation"`. Also note, matching Security's section's
own honesty about it: `resolve_chat_model` still doesn't exist, so BYOK still doesn't
functionally work here either — this task only added regression tests for the
already-correct fallback behavior, it didn't implement the missing resolver.

- [ ] **Step 7: Commit**

```bash
git add backend/agents_orchestrator/documentation_agent/prompts/doc_prompt.py frontend/app/\(app\)/projects/\[id\]/page.tsx help/portfolio-1-agent-status.md
git commit -m "feat: complete the Documentation agent — RTM honesty fix, go live

RTM deliverable now explicitly distinguishes the two columns with real
structural IDs (Requirement, Code Review) from the four that don't
(Design, Development, Testing, Security) -- those either say N/A or
carry an explicit 'Inferred, verify manually' prefix, never presented
with the same confidence as real evidence. builtAgents now includes
'documentation', matching the verification done in Tasks 1-2.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
