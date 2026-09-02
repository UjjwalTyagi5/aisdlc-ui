"""The PM agent: its tools, its prompt's honesty, and its route's authorisation.

PHASE 2 — work breakdown and estimation. Scheduling and assignment are phase 3, and
several of these tests exist to keep the agent from IMPLYING otherwise: an agent that
presents a task list as a committed sprint plan is the same class of false success as an
upload that reports success into an empty container.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.pm_agent.agents import schedule as mod  # noqa: E402

TENANT = "81a736f4-cd44-4f63-842c-ae57023d0346"
PROJECT = "f45e7d23-c821-44b3-a88b-6175f67ddef0"


def _bind(monkeypatch, project=PROJECT, tenant=TENANT):
    import config.ws_helper as ws

    monkeypatch.setattr(ws, "get_project_id", lambda: project)
    monkeypatch.setattr(ws, "get_tenant_id", lambda: tenant)
    monkeypatch.setattr(ws, "get_session_id", lambda: "s1")


# -- the module itself ---------------------------------------------------------


@pytest.mark.unit
def test_it_is_not_a_third_agents_planning_module():
    """Requirements and Design both ship `agents/planning.py`, and under pytest the
    dotted name resolved to the wrong one — a shadowing hazard already hit once here. A
    third copy would make it a coin toss."""
    assert mod.__name__.endswith(".schedule")
    assert not mod.__file__.endswith("planning.py")


@pytest.mark.unit
def test_the_tools_it_needs_are_bound():
    names = {t.name for t in mod.tools}
    assert {"read_project_inputs", "list_sprints", "read_team_capacity", "save_plan"} <= names


@pytest.mark.unit
def test_it_can_produce_documents_like_the_other_agents():
    """A plan people act on gets exported. Both other agents have these; a planner
    without them would be the one stage whose output cannot leave the screen."""
    names = {t.name for t in mod.tools}
    assert "export_document" in names and "generate_diagram" in names


# -- reading the inputs --------------------------------------------------------


@pytest.mark.unit
async def test_it_says_so_when_there_is_no_project(monkeypatch):
    """Returning "" would read to the model as "this project has no requirements",
    which is a different and wrong statement."""
    _bind(monkeypatch, project=None, tenant=None)
    out = await mod.read_project_inputs.ainvoke({})
    assert "not attached to a project" in out


@pytest.mark.unit
async def test_it_distinguishes_no_project_from_no_requirements(monkeypatch):
    from config import context_broker

    _bind(monkeypatch)

    async def _empty(*_a, **_kw):
        return ""

    monkeypatch.setattr(context_broker, "build_context_for_project", _empty)
    out = await mod.read_project_inputs.ainvoke({})
    assert "no requirements or design recorded yet" in out


@pytest.mark.unit
async def test_it_reads_both_upstream_stages(monkeypatch):
    """The planner is registered with requirements AND design as inputs; reading only
    one would plan components with nothing tying them to what was asked for."""
    from config import context_broker

    _bind(monkeypatch)
    seen = {}

    async def _ctx(project_id, tenant_id, agent_id):
        seen["agent_id"] = agent_id
        return "[REQUIREMENTS]\n…\n[DESIGN]\n…"

    monkeypatch.setattr(context_broker, "build_context_for_project", _ctx)
    await mod.read_project_inputs.ainvoke({})
    assert seen["agent_id"] == "plan"


# -- sprints and capacity ------------------------------------------------------


@pytest.mark.unit
async def test_a_board_with_no_sprints_is_not_an_error(monkeypatch):
    """Kanban is a legitimate setup. Raising would make the planner treat it as a
    failure instead of planning in dated phases."""
    _bind(monkeypatch)
    _fake_connector(monkeypatch, {"list_sprints": []})

    out = await mod.list_sprints.ainvoke({})
    assert "no sprints" in out and "dated phases" in out


@pytest.mark.unit
async def test_missing_capacity_asks_rather_than_assumes(monkeypatch):
    """Jira has no capacity API. Assuming a full sprint each would be a plan that looks
    authoritative and overcommits everybody."""
    _bind(monkeypatch)
    _fake_connector(monkeypatch, {"team_capacity": []})

    out = await mod.read_team_capacity.ainvoke({"iteration_id": "it-1"})
    assert "Ask the user" in out
    assert "assuming a full sprint" in out


@pytest.mark.unit
async def test_the_board_project_comes_from_the_ingest_payload(monkeypatch):
    """There is no context variable for it — `ingest_board` records it in
    requirements_payload and that is the only place it is written."""
    from config import context_broker

    _bind(monkeypatch)

    async def _artifacts(_p, _t):
        return {"requirements_payload": {"board_project": "My Software Team"}}

    monkeypatch.setattr(context_broker, "_fetch_artifacts_for_project", _artifacts)
    assert await mod._board_project() == "My Software Team"


@pytest.mark.unit
async def test_an_unresolvable_board_name_is_empty_not_fatal(monkeypatch):
    """A board name is a convenience. Failing a tool for it would break planning on a
    project that simply has not pulled stories yet."""
    from config import context_broker

    _bind(monkeypatch)

    async def _boom(_p, _t):
        raise RuntimeError("db down")

    monkeypatch.setattr(context_broker, "_fetch_artifacts_for_project", _boom)
    assert await mod._board_project() == ""


# -- saving --------------------------------------------------------------------


@pytest.mark.unit
async def test_saving_without_a_project_refuses_clearly(monkeypatch):
    _bind(monkeypatch, project=None, tenant=None)
    out = await mod.save_plan.ainvoke({"tasks_json": "[]"})
    assert "nowhere to save" in out


@pytest.mark.unit
async def test_malformed_json_is_reported_not_swallowed(monkeypatch):
    """The model writes these arguments. A silent drop would save an empty plan and
    report success."""
    _bind(monkeypatch)
    out = await mod.save_plan.ainvoke({"tasks_json": "{not json"})
    assert "Could not save" in out and "tasks" in out


@pytest.mark.unit
async def test_a_partial_plan_is_still_savable(monkeypatch):
    """A breakdown with no schedule yet is a legitimate intermediate state; refusing it
    would lose work the user just approved."""
    captured = _fake_persistence(monkeypatch)
    _bind(monkeypatch)

    out = await mod.save_plan.ainvoke({"tasks_json": json.dumps([{"title": "Login"}])})
    assert "Plan saved" in out
    assert captured["payload"]["tasks"] == [{"title": "Login"}]
    assert captured["payload"]["schedule"] is None


@pytest.mark.unit
async def test_the_first_save_sets_the_baseline(monkeypatch):
    captured = _fake_persistence(monkeypatch, existing=None)
    _bind(monkeypatch)

    await mod.save_plan.ainvoke({"schedule_json": json.dumps([{"sprint": "S1"}])})
    assert captured["payload"]["baseline"] == {"schedule": [{"sprint": "S1"}]}


@pytest.mark.unit
async def test_a_later_save_does_not_move_the_baseline(monkeypatch):
    """"How far have we slipped" is answerable only against what was originally
    committed to. A baseline that follows the current plan always reports zero slip."""
    original = {"schedule": [{"sprint": "S1"}]}
    captured = _fake_persistence(monkeypatch, existing={"baseline": original})
    _bind(monkeypatch)

    await mod.save_plan.ainvoke({"schedule_json": json.dumps([{"sprint": "S2"}])})
    assert captured["payload"]["baseline"] == original


@pytest.mark.unit
async def test_saving_says_it_is_awaiting_approval(monkeypatch):
    """It is not the committed plan until a project admin says so, and claiming
    otherwise sets an expectation nobody agreed to."""
    _fake_persistence(monkeypatch)
    _bind(monkeypatch)

    out = await mod.save_plan.ainvoke({"tasks_json": "[]"})
    assert "awaiting a project admin's approval" in out


# -- the prompt ----------------------------------------------------------------


@pytest.mark.unit
def test_the_prompt_refuses_to_fake_a_schedule():
    """THE HONESTY TEST. Sequencing and assignment are not built. An agent that
    improvises a sprint plan and presents it as one is worse than one that declines."""
    p = " ".join(mod.PM_SYS_MESSAGE.split())
    assert "YOU DO NOT YET BUILD SCHEDULES OR ASSIGN PEOPLE" in p
    assert "Do NOT improvise a sprint plan and present it as one" in p


@pytest.mark.unit
def test_the_prompt_forbids_inventing_estimates():
    p = " ".join(mod.PM_SYS_MESSAGE.split())
    assert "NEVER invent an estimate" in p
    assert "Needs sizing with the team" in p


@pytest.mark.unit
def test_the_prompt_names_the_capacity_gap():
    """The model has to know Jira cannot answer this, or it will present an assumption
    as a reading."""
    p = " ".join(mod.PM_SYS_MESSAGE.split())
    assert "Jira has NO capacity API" in p
    assert "Never present an assumed capacity as if it were read from the board" in p


@pytest.mark.unit
def test_the_prompt_does_not_let_it_claim_the_plan_is_committed():
    p = " ".join(mod.PM_SYS_MESSAGE.split())
    assert 'do NOT say it has been "committed" or "approved"' in p


# -- the route -----------------------------------------------------------------


@pytest.mark.unit
def test_both_surfaces_exist():
    """REST for scripted use, WS because that is what the chat drawer speaks."""
    from agents_orchestrator.pm_agent.pm_agent_api import pm_router_orchestrator as r

    paths = {x.path for x in r.routes}
    assert {"/chat/", "/ws", "/health"} <= paths


@pytest.mark.unit
def test_neither_surface_trusts_the_form_user_id():
    """Trusting it let any authenticated caller claim to be anyone — the bug this
    codebase already fixed once on the Design route."""
    import inspect

    from agents_orchestrator.pm_agent import pm_agent_api as api

    src = inspect.getsource(api)
    assert 'getattr(request.state, "user_id"' in src
    assert "NOT trusted for identity" in src


@pytest.mark.unit
def test_the_websocket_checks_agent_access_too():
    """A WS turn that skipped it would be a way around the permission REST enforces."""
    import inspect

    from agents_orchestrator.pm_agent import pm_agent_api as api

    assert "assert_agent_access_for_chat" in inspect.getsource(api._process_turn_ws)


@pytest.mark.unit
def test_the_reply_never_echoes_tool_output():
    """A ToolMessage carries whatever the tool returned — a whole context dump, a JSON
    array of sprints. Appending it is how the Design agent once showed a user its own
    document twice."""
    from langchain_core.messages import AIMessage, ToolMessage

    from agents_orchestrator.pm_agent.pm_agent_api import _last_reply

    out = _last_reply([
        AIMessage(content="Here is the breakdown."),
        ToolMessage(content="<<a giant dump>>", tool_call_id="1", name="read_project_inputs"),
    ])
    assert out == "Here is the breakdown."


# -- helpers -------------------------------------------------------------------


def _fake_connector(monkeypatch, answers: dict):
    from agents_orchestrator.requirements_agent.agents import planning as req

    class _C:
        async def read_adapter(self, operation, **_kw):
            return answers.get(operation, [])

    async def _conn(mode="read", provider=""):
        return _C(), None

    monkeypatch.setattr(req, "_board_connector", _conn)


def _fake_persistence(monkeypatch, existing=None):
    """Capture what save_plan would persist, without a database."""
    captured: dict = {}

    class _Session:
        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    async def _run(_s, _t, _p, _stage):
        return "11111111-1111-1111-1111-111111111111"

    async def _persist(run_id, artifact_type, payload, tenant_id=None):
        captured["run_id"] = run_id
        captured["type"] = artifact_type
        captured["payload"] = payload

    async def _existing(_t, _r):
        return existing

    import shared.db as shared_db
    import shared.services.artifact_service as art
    import shared.services.chat_artifacts as chat

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", lambda _t: _Session())
    monkeypatch.setattr(chat, "_get_or_create_chat_run", _run)
    monkeypatch.setattr(art, "persist_artifact", _persist)
    monkeypatch.setattr(mod, "_existing_plan", _existing)
    return captured
