"""Re-planning and costing — the delta, and the two budgets that must not be added.

PHASE 5.

RE-PLANNING IS THE DELTA, NOT THE NEW PLAN. "What does this change cost us" is the
question actually being asked. A function that hands back a fresh schedule and leaves
the reader to diff two lists is how a re-plan gets waved through without anyone seeing
what it did.

THE TWO BUDGETS ARE NOT THE SAME BUDGET, and this is the trap the whole costing design
is shaped around. The platform meters LLM spend — what running the agents costs — against
a per-project cap. It stores NO labour rate and knows nothing about what a team costs.
Answering "can we afford this plan" from the LLM budget would be confidently wrong, so
the two live in separate tools that both say what they are.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.pm_agent.agents import schedule as agent  # noqa: E402
from agents_orchestrator.pm_agent.scheduling import (  # noqa: E402
    apply_changes, replan, tasks_from_schedule,
)


def _t(tid, estimate=5, **kw):
    return {"id": tid, "title": f"Task {tid}", "estimate": estimate, **kw}


def _slot(name, items, capacity=20):
    return {"id": name, "name": name, "capacity": capacity,
            "committed": sum(t["estimate"] or 0 for t in items), "items": items}


# -- applying changes ----------------------------------------------------------


@pytest.mark.unit
def test_a_task_can_be_added():
    tasks, rejected = apply_changes([_t("a")], [{"op": "add", "id": "b", "estimate": 3}])
    assert {str(t["id"]) for t in tasks} == {"a", "b"}
    assert rejected == []


@pytest.mark.unit
def test_a_task_can_be_removed():
    tasks, _ = apply_changes([_t("a"), _t("b")], [{"op": "remove", "id": "b"}])
    assert {str(t["id"]) for t in tasks} == {"a"}


@pytest.mark.unit
def test_a_task_can_be_resized():
    tasks, _ = apply_changes([_t("a", 5)], [{"op": "reestimate", "id": "a", "estimate": 13}])
    assert tasks[0]["estimate"] == 13


@pytest.mark.unit
@pytest.mark.parametrize(
    "change",
    [
        {"op": "reestimate", "id": "ghost", "estimate": 3},
        {"op": "remove", "id": "ghost"},
        {"op": "sprinkle", "id": "a"},
    ],
)
def test_a_change_naming_work_that_is_not_there_is_rejected(change):
    """"Re-estimate T7" when there is no T7 is usually a typo, and inventing T7 would
    put phantom work in the plan."""
    tasks, rejected = apply_changes([_t("a")], [change])
    assert len(rejected) == 1
    assert {str(t["id"]) for t in tasks} == {"a"}


@pytest.mark.unit
def test_adding_something_already_there_is_rejected_not_duplicated():
    tasks, rejected = apply_changes([_t("a")], [{"op": "add", "id": "a", "estimate": 99}])
    assert len(tasks) == 1 and tasks[0]["estimate"] == 5
    assert "already in the plan" in rejected[0]


# -- the delta -----------------------------------------------------------------


@pytest.mark.unit
def test_it_reports_which_work_slipped_a_sprint():
    """THE POINT OF THE TOOL. Growing an early task pushes a later one out, and that
    displacement is what a manager needs to see."""
    current = [_slot("S1", [_t("a", 5), _t("b", 5)]), _slot("S2", [])]
    out = replan(
        current, [{"op": "reestimate", "id": "a", "estimate": 18}],
        [{"id": "S1", "name": "S1", "capacity": 20}, {"id": "S2", "name": "S2", "capacity": 20}],
    )
    assert out["moved"] == [{"task": "b", "from": "S1", "to": "S2"}]


@pytest.mark.unit
def test_it_reports_what_fell_out_of_the_plan_entirely():
    current = [_slot("S1", [_t("a", 5)])]
    out = replan(current, [{"op": "reestimate", "id": "a", "estimate": 500}],
                 [{"id": "S1", "name": "S1", "capacity": 20}])
    assert out["no_longer_scheduled"] == [{"task": "a", "was": "S1"}]
    assert "larger than any sprint" in out["unscheduled"][0]["reason"]


@pytest.mark.unit
def test_it_reports_how_much_the_commitment_grew():
    current = [_slot("S1", [_t("a", 5)])]
    out = replan(current, [{"op": "add", "id": "b", "estimate": 8}],
                 [{"id": "S1", "name": "S1", "capacity": 20}])
    assert out["committed_before"] == 5
    assert out["committed_after"] == 13
    assert out["committed_change"] == 8


@pytest.mark.unit
def test_newly_scheduled_work_is_named():
    current = [_slot("S1", [_t("a", 5)])]
    out = replan(current, [{"op": "add", "id": "b", "estimate": 3}],
                 [{"id": "S1", "name": "S1", "capacity": 20}])
    assert out["newly_scheduled"] == ["b"]


@pytest.mark.unit
def test_rejected_changes_travel_with_the_result():
    """A user who believes a rejected change took effect is planning against something
    that does not exist."""
    out = replan([_slot("S1", [_t("a", 5)])], [{"op": "remove", "id": "ghost"}],
                 [{"id": "S1", "name": "S1", "capacity": 20}])
    assert out["changes_rejected"] and "ghost" in out["changes_rejected"][0]


@pytest.mark.unit
def test_drift_from_the_baseline_is_separate_from_drift_since_last_week():
    """Different questions. A plan can be unchanged since Friday and far from what was
    originally agreed."""
    current = [_slot("S1", [_t("a", 10)])]
    out = replan(current, [{"op": "add", "id": "b", "estimate": 5}],
                 [{"id": "S1", "name": "S1", "capacity": 20}],
                 baseline=[{"name": "S1", "committed": 8}])
    assert out["committed_change"] == 5      # since the current plan
    assert out["vs_baseline"] == 7           # since the original commitment


@pytest.mark.unit
def test_tasks_are_recovered_from_a_schedule():
    assert {str(t["id"]) for t in tasks_from_schedule(
        [_slot("S1", [_t("a")]), _slot("S2", [_t("b")])]
    )} == {"a", "b"}


# -- the tools -----------------------------------------------------------------


@pytest.mark.unit
async def test_replan_without_changes_says_the_plan_is_unchanged():
    out = await agent.replan.ainvoke({
        "schedule_json": json.dumps([_slot("S1", [_t("a")])]), "changes_json": "[]",
    })
    assert "unchanged" in out


@pytest.mark.unit
async def test_replan_reuses_the_current_sprints_when_none_are_given():
    """Inventing sprint boundaries during a re-plan would silently change the shape of
    the plan while claiming only to have applied a change."""
    current = [_slot("S1", [_t("a", 5)], capacity=20)]
    out = json.loads(await agent.replan.ainvoke({
        "schedule_json": json.dumps(current),
        "changes_json": json.dumps([{"op": "add", "id": "b", "estimate": 3}]),
    }))
    assert [s["name"] for s in out["schedule"]] == ["S1"]
    assert out["schedule"][0]["capacity"] == 20


# -- costing -------------------------------------------------------------------


@pytest.mark.unit
async def test_costing_needs_rates_and_will_not_invent_one():
    """A total built on an assumed day rate is a number somebody puts in front of a
    client."""
    out = await agent.cost_plan.ainvoke({
        "schedule_json": json.dumps([_slot("S1", [_t("a", 10)])]), "rates_json": "",
    })
    assert "stores no labour rate" in out


@pytest.mark.unit
async def test_it_costs_effort_at_the_supplied_rate():
    out = json.loads(await agent.cost_plan.ainvoke({
        "schedule_json": json.dumps([_slot("S1", [_t("a", 10, assigned_to="Ana")])]),
        "rates_json": json.dumps([{"name": "Ana", "rate": 85}]),
    }))
    assert out["total"] == 850.0
    assert out["kind"] == "labour_cost"


@pytest.mark.unit
async def test_effort_with_no_rate_is_excluded_and_reported():
    """Charging it at a guess would hide the gap inside a confident total."""
    out = json.loads(await agent.cost_plan.ainvoke({
        "schedule_json": json.dumps([_slot("S1", [
            _t("a", 10, assigned_to="Ana"), _t("b", 4, assigned_to="Bo"),
        ])]),
        "rates_json": json.dumps([{"name": "Ana", "rate": 85}]),
    }))
    assert out["total"] == 850.0
    assert out["uncosted_effort"] == 4
    assert any("Bo" in n for n in out["notes"])


@pytest.mark.unit
async def test_a_default_rate_covers_the_rest_when_the_user_supplies_one():
    out = json.loads(await agent.cost_plan.ainvoke({
        "schedule_json": json.dumps([_slot("S1", [_t("a", 10)])]),
        "rates_json": json.dumps([{"default": 50}]),
    }))
    assert out["total"] == 500.0
    assert out["uncosted_effort"] == 0


@pytest.mark.unit
async def test_the_cost_says_it_is_not_the_llm_budget():
    out = json.loads(await agent.cost_plan.ainvoke({
        "schedule_json": json.dumps([_slot("S1", [_t("a", 10)])]),
        "rates_json": json.dumps([{"default": 50}]),
    }))
    assert any("not comparable with budget_status" in n.lower() for n in out["notes"])


@pytest.mark.unit
async def test_the_budget_tool_says_which_budget_it_is(monkeypatch):
    """The whole risk here is a reader taking an LLM cap for a project budget."""
    import config.ws_helper as ws
    import shared.services.budget_store as store
    from sqlalchemy import select  # noqa: F401

    monkeypatch.setattr(ws, "get_project_id", lambda: "p1")
    monkeypatch.setattr(ws, "get_tenant_id", lambda: "t1")

    class _Session:
        async def execute(self, _stmt):
            class _R:
                @staticmethod
                def scalar_one_or_none():
                    return 1000
            return _R()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    async def _spend(_scope, _id):
        return 12.5

    import shared.db as shared_db

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", lambda _t: _Session())
    monkeypatch.setattr(store, "read_scope_spend", _spend)

    out = json.loads(await agent.budget_status.ainvoke({}))
    assert out["kind"] == "llm_spend"
    assert out["remaining_usd"] == 987.5
    assert "not a labour budget" in out["note"]


@pytest.mark.unit
async def test_no_project_means_no_budget_reading(monkeypatch):
    import config.ws_helper as ws

    monkeypatch.setattr(ws, "get_project_id", lambda: None)
    monkeypatch.setattr(ws, "get_tenant_id", lambda: None)
    assert "not attached to a project" in await agent.budget_status.ainvoke({})


# -- the prompt ----------------------------------------------------------------


@pytest.mark.unit
def test_the_prompt_keeps_the_two_budgets_apart():
    p = " ".join(agent.PM_SYS_MESSAGE.split())
    assert "TWO BUDGETS, NEVER ADDED TOGETHER" in p
    assert "Do NOT sum them" in p
    assert "The platform stores NO labour rate" in p


@pytest.mark.unit
def test_the_prompt_requires_reporting_the_delta():
    p = " ".join(agent.PM_SYS_MESSAGE.split())
    assert "REPORT THE DELTA" in p
    assert "changes_rejected" in p


@pytest.mark.unit
def test_the_phase_5_tools_are_bound():
    assert {"replan", "budget_status", "cost_plan"} <= {t.name for t in agent.tools}
