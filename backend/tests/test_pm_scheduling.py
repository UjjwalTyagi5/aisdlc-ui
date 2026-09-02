"""Sequencing and levelling — the arithmetic, and what it refuses to guess.

PHASE 3. This is deliberately code rather than prompt: topological ordering, adding
hours against a ceiling, and spotting that somebody is booked for 60 hours in a 40-hour
sprint are what a model does badly and a function does reliably. A model asked to
"schedule these" produces something that LOOKS like a plan and is wrong in ways nobody
sees without redoing the arithmetic.

MOST OF THESE TESTS ARE ABOUT WHAT IT WILL NOT DO. Three situations have no correct
answer — an unestimated item, an item bigger than any sprint, a dependency cycle — and
each is reported with a reason rather than papered over. A plan honest about what it
could not place is more useful than one that placed everything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.pm_agent.scheduling import (  # noqa: E402
    allocate, build_schedule, order_by_dependencies, sprint_capacity,
)


def _t(tid, estimate=None, depends_on=None, **kw):
    return {"id": tid, "title": f"Task {tid}", "estimate": estimate,
            "depends_on": depends_on or [], **kw}


def _s(sid, capacity=None, **kw):
    return {"id": sid, "name": f"Sprint {sid}", "capacity": capacity, **kw}


# -- ordering ------------------------------------------------------------------


@pytest.mark.unit
def test_a_dependency_comes_before_its_dependent():
    ordered, cyclic = order_by_dependencies([_t("b", depends_on=["a"]), _t("a")])
    assert [t["id"] for t in ordered] == ["a", "b"]
    assert cyclic == []


@pytest.mark.unit
def test_a_cycle_is_reported_not_ordered_arbitrarily():
    """A plan whose order is arbitrary in one place cannot be trusted anywhere, so the
    cycle comes back separately instead of being appended in whatever order."""
    ordered, cyclic = order_by_dependencies([
        _t("a", depends_on=["b"]), _t("b", depends_on=["a"]), _t("c"),
    ])
    assert [t["id"] for t in ordered] == ["c"]
    assert {t["id"] for t in cyclic} == {"a", "b"}


@pytest.mark.unit
def test_a_dependency_on_something_outside_the_set_is_ignored():
    """It usually means the prerequisite is already done or lives on another team's
    board. Blocking the whole schedule on it would be wrong."""
    ordered, cyclic = order_by_dependencies([_t("a", depends_on=["already-done"])])
    assert [t["id"] for t in ordered] == ["a"]
    assert cyclic == []


@pytest.mark.unit
def test_a_task_depending_on_itself_does_not_deadlock():
    ordered, cyclic = order_by_dependencies([_t("a", depends_on=["a"])])
    assert [t["id"] for t in ordered] == ["a"]


# -- capacity ------------------------------------------------------------------


@pytest.mark.unit
def test_an_explicit_capacity_wins():
    assert sprint_capacity(_s("1", capacity=40)) == 40.0


@pytest.mark.unit
def test_capacity_is_derived_from_the_team_when_the_sprint_has_none():
    sprint = {"start_date": "2026-09-01", "finish_date": "2026-09-14"}   # 14 days -> 10 working
    team = [{"name": "Ana", "capacity_per_day": 6, "days_off": 0}]
    assert sprint_capacity(sprint, team) == 60.0


@pytest.mark.unit
def test_days_off_come_out_of_capacity():
    """A capacity number that ignores leave looks authoritative and overcommits the
    person — worse than having no number."""
    sprint = {"start_date": "2026-09-01", "finish_date": "2026-09-14"}
    full = sprint_capacity(sprint, [{"name": "Ana", "capacity_per_day": 6, "days_off": 0}])
    with_leave = sprint_capacity(sprint, [{"name": "Ana", "capacity_per_day": 6, "days_off": 2}])
    assert with_leave < full


@pytest.mark.unit
def test_more_days_off_than_the_sprint_does_not_give_capacity_back():
    sprint = {"start_date": "2026-09-01", "finish_date": "2026-09-07"}
    cap = sprint_capacity(sprint, [{"name": "Ana", "capacity_per_day": 6, "days_off": 99}])
    assert cap is None or cap >= 0


@pytest.mark.unit
def test_unknown_capacity_is_none_not_a_guess():
    """A schedule built against an invented ceiling is worse than one that says it does
    not know the ceiling."""
    assert sprint_capacity({"name": "S1"}) is None
    assert sprint_capacity({"name": "S1"}, []) is None


# -- packing -------------------------------------------------------------------


@pytest.mark.unit
def test_work_fills_the_first_sprint_with_room():
    out = build_schedule([_t("a", 5), _t("b", 5)], [_s("1", capacity=10), _s("2", capacity=10)])
    assert [t["id"] for t in out["schedule"][0]["items"]] == ["a", "b"]
    assert out["schedule"][0]["committed"] == 10
    assert out["unscheduled"] == []


@pytest.mark.unit
def test_it_overflows_into_the_next_sprint_rather_than_over_committing():
    out = build_schedule([_t("a", 8), _t("b", 5)], [_s("1", capacity=10), _s("2", capacity=10)])
    assert [t["id"] for t in out["schedule"][0]["items"]] == ["a"]
    assert [t["id"] for t in out["schedule"][1]["items"]] == ["b"]


@pytest.mark.unit
def test_a_dependent_never_lands_before_its_prerequisite():
    """Otherwise a task is committed alongside work it needs to have finished."""
    out = build_schedule(
        [_t("b", 5, depends_on=["a"]), _t("a", 5)],
        [_s("1", capacity=5), _s("2", capacity=5)],
    )
    sprint_of = {
        t["id"]: i for i, s in enumerate(out["schedule"]) for t in s["items"]
    }
    assert sprint_of["a"] < sprint_of["b"]


# -- what it refuses to guess --------------------------------------------------


@pytest.mark.unit
def test_an_unestimated_task_is_not_scheduled():
    """Assuming a default silently invents scope."""
    out = build_schedule([_t("a")], [_s("1", capacity=10)])
    assert out["schedule"][0]["items"] == []
    assert "no estimate" in out["unscheduled"][0]["reason"]


@pytest.mark.unit
def test_a_zero_estimate_is_a_real_estimate():
    """The other half of the same rule — 0 is a value somebody entered."""
    out = build_schedule([_t("a", 0)], [_s("1", capacity=10)])
    assert [t["id"] for t in out["schedule"][0]["items"]] == ["a"]


@pytest.mark.unit
def test_a_task_bigger_than_any_sprint_says_so():
    """It can never fit. Stretching a sprint to hold it is a decision, not arithmetic."""
    out = build_schedule([_t("a", 100)], [_s("1", capacity=10), _s("2", capacity=10)])
    reason = out["unscheduled"][0]["reason"]
    assert "larger than any sprint" in reason and "split it" in reason


@pytest.mark.unit
def test_a_cyclic_task_is_unscheduled_with_its_reason():
    out = build_schedule(
        [_t("a", 1, depends_on=["b"]), _t("b", 1, depends_on=["a"])],
        [_s("1", capacity=10)],
    )
    assert len(out["unscheduled"]) == 2
    assert all("cycle" in u["reason"] for u in out["unscheduled"])


@pytest.mark.unit
def test_nothing_is_silently_dropped():
    """Every task ends up in exactly one of the two lists. A scheduler that loses one
    produces a plan missing work nobody notices is missing."""
    tasks = [_t("a", 5), _t("b"), _t("c", 500), _t("d", 1, depends_on=["e"]), _t("e", 1, depends_on=["d"])]
    out = build_schedule(tasks, [_s("1", capacity=10)])
    placed = {t["id"] for s in out["schedule"] for t in s["items"]}
    missed = {u["task"]["id"] for u in out["unscheduled"]}
    assert placed | missed == {t["id"] for t in tasks}
    assert not (placed & missed)


@pytest.mark.unit
def test_no_sprints_is_reported_rather_than_crashing():
    out = build_schedule([_t("a", 5)], [])
    assert "no sprints" in out["unscheduled"][0]["reason"]


@pytest.mark.unit
def test_a_sprint_with_unknown_capacity_says_nothing_limited_it():
    """It will accept everything, and a reader has to know that was not a judgement."""
    out = build_schedule([_t("a", 500)], [_s("1")])
    assert [t["id"] for t in out["schedule"][0]["items"]] == ["a"]
    assert any("no capacity known" in n for n in out["notes"])


# -- allocation ----------------------------------------------------------------


@pytest.mark.unit
def test_work_goes_to_the_least_loaded_person():
    schedule = [{"name": "S1", "items": [_t("a", 5), _t("b", 5)]}]
    out = allocate(schedule, [{"name": "Ana", "hours": 40}, {"name": "Bo", "hours": 40}])
    assert sorted(p["assigned"] for p in out["assignments"]) == [5, 5]


@pytest.mark.unit
def test_an_existing_assignment_is_respected():
    """Somebody decided that. A scheduler that reshuffles real assignments is one people
    stop trusting."""
    schedule = [{"name": "S1", "items": [_t("a", 5, assigned_to="Bo"), _t("b", 5)]}]
    out = allocate(schedule, [{"name": "Ana", "hours": 40}, {"name": "Bo", "hours": 40}])
    bo = next(p for p in out["assignments"] if p["name"] == "Bo")
    assert any(i["preassigned"] for i in bo["items"])


@pytest.mark.unit
def test_over_allocation_is_reported_with_the_amount():
    """Refusing to assign would leave work with nobody on it; assigning silently would
    commit somebody past their hours. Saying who is over is the only honest option."""
    schedule = [{"name": "S1", "items": [_t("a", 30), _t("b", 30)]}]
    out = allocate(schedule, [{"name": "Ana", "hours": 40}])
    assert out["over_allocated"][0]["name"] == "Ana"
    assert out["over_allocated"][0]["over_by"] == 20


@pytest.mark.unit
def test_unknown_capacity_is_not_treated_as_zero():
    """Zero would mark somebody over-allocated by their first task; unknown means the
    plan cannot check, and it says so."""
    schedule = [{"name": "S1", "items": [_t("a", 5)]}]
    out = allocate(schedule, [{"name": "Ana"}])
    assert out["over_allocated"] == []
    assert any("No capacity known" in n for n in out["notes"])


@pytest.mark.unit
def test_no_team_assigns_nothing_and_says_why():
    out = allocate([{"name": "S1", "items": [_t("a", 5)]}], [])
    assert out["assignments"] == []
    assert "No team capacity" in out["notes"][0]


# -- the prompt keeps the arithmetic out of the model --------------------------


@pytest.mark.unit
def test_the_prompt_forbids_doing_the_sums_itself():
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    p = " ".join(PM_SYS_MESSAGE.split())
    assert "THE ARITHMETIC IS NOT YOURS TO DO" in p
    assert "Do NOT work out the packing" in p


@pytest.mark.unit
def test_the_prompt_requires_relaying_what_could_not_be_placed():
    """Those entries are the decisions only a person can make."""
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    p = " ".join(PM_SYS_MESSAGE.split())
    assert "RELAY `unscheduled` AND `over_allocated` VERBATIM" in p


@pytest.mark.unit
def test_the_agent_no_longer_claims_it_cannot_schedule():
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE, tools

    names = {t.name for t in tools}
    assert {"build_schedule", "allocate_resources"} <= names
    assert "YOU DO NOT YET BUILD SCHEDULES" not in PM_SYS_MESSAGE
