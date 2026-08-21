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


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


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
