"""Risk exposure and progress reporting — and the four questions it refuses to answer.

PHASE 4. The arithmetic is in code for the same reason as phase 3: "are we on track" has
a numeric answer, and a model estimating it produces a confident sentence nobody can
check.

THE HARD PART IS KNOWING WHEN NOT TO ANSWER, and most of this file is that. Each of
these has a plausible wrong answer that reads as fact and gets planned around:

  a velocity from one sprint          one data point is not a trend
  a forecast when nothing is done     "0 sprints" reads as finished
  slippage with no baseline           comparing a plan to itself always says zero
  a board state this does not know    finished work silently counts as outstanding
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.pm_agent.reporting import (  # noqa: E402
    assess_risks, compare_to_baseline, forecast_completion, summarise_progress,
    velocity_from,
)


def _task(tid, estimate=5, state=None, title=None):
    return {"id": tid, "title": title or f"Task {tid}", "estimate": estimate, "state": state}


def _sprint(name, items):
    return {"name": name, "items": items}


# -- progress ------------------------------------------------------------------


@pytest.mark.unit
def test_it_totals_committed_and_completed_per_sprint():
    out = summarise_progress([
        _sprint("S1", [_task("a", 5, "Done"), _task("b", 3, "Active")]),
    ])
    s = out["sprints"][0]
    assert (s["committed"], s["completed"], s["remaining"]) == (8, 5, 3)


@pytest.mark.unit
def test_completion_is_read_from_the_board_not_the_plan():
    """A plan does not know what got finished. The board does, and if the two disagree
    the board is right."""
    schedule = [_sprint("S1", [_task("a", 5, "Active")])]
    board = [{"id": "a", "state": "Done"}]
    assert summarise_progress(schedule, board)["total_completed"] == 5


@pytest.mark.unit
def test_a_plan_reported_without_board_items_says_so():
    """Otherwise a reader takes "3 of 8 done" as a fact about the team rather than
    about the plan's own stale copy."""
    out = summarise_progress([_sprint("S1", [_task("a", 5, "Done")])])
    assert any("No board items were supplied" in n for n in out["notes"])


@pytest.mark.unit
def test_an_unrecognised_done_state_is_named_not_assumed():
    """THE QUIET ONE. A board whose final column is "Shipped to prod" would have every
    finished item counted as outstanding, and the project would look stalled while the
    team was done."""
    out = summarise_progress(
        [_sprint("S1", [_task("a", 5, "Shipped to prod")])]
    )
    assert out["total_completed"] == 0
    assert "Shipped to prod" in out["unrecognised_states"]
    assert any("not recognised as finished" in n for n in out["notes"])


@pytest.mark.unit
def test_a_caller_can_say_what_done_means_on_their_board():
    out = summarise_progress(
        [_sprint("S1", [_task("a", 5, "Shipped to prod")])],
        done_states={"shipped to prod"},
    )
    assert out["total_completed"] == 5
    assert out["unrecognised_states"] == []


@pytest.mark.unit
def test_unestimated_items_are_counted_and_flagged():
    """They contribute 0 to the totals, so the real remaining work is higher — and a
    reader has to be told, or the burndown quietly lies."""
    out = summarise_progress([_sprint("S1", [_task("a", None, "Active"), _task("b", 5, "Active")])])
    assert out["total_committed"] == 5
    assert any("no estimate" in n for n in out["notes"])


@pytest.mark.unit
@pytest.mark.parametrize("state", ["done", "DONE", " Done "])
def test_done_detection_ignores_case_and_padding(state):
    out = summarise_progress([_sprint("S1", [_task("a", 5, state)])])
    assert out["total_completed"] == 5


# -- velocity ------------------------------------------------------------------


@pytest.mark.unit
def test_one_sprint_is_not_a_velocity():
    """Averaging a single number produces something that looks like a trend and will be
    planned around."""
    out = velocity_from([{"committed": 10, "completed": 10}])
    assert out["velocity"] is None
    assert "1 sprint" in out["reason"]


@pytest.mark.unit
def test_two_sprints_give_an_average():
    out = velocity_from([
        {"committed": 10, "completed": 8},
        {"committed": 10, "completed": 12},
    ])
    assert out["velocity"] == 10.0
    assert out["sprints_measured"] == 2


@pytest.mark.unit
def test_empty_sprints_do_not_drag_the_average_down():
    """A sprint nothing was committed to says nothing about the team's rate."""
    out = velocity_from([
        {"committed": 10, "completed": 10},
        {"committed": 10, "completed": 10},
        {"committed": 0, "completed": 0},
    ])
    assert out["velocity"] == 10.0
    assert out["sprints_measured"] == 2


@pytest.mark.unit
def test_wild_variation_is_flagged_rather_than_averaged_away():
    """A mean over 2 and 30 is arithmetically fine and practically misleading."""
    out = velocity_from([
        {"committed": 30, "completed": 2},
        {"committed": 30, "completed": 30},
    ])
    assert out["velocity"] is not None
    assert "variation is larger than the average" in out["reason"]


# -- forecast ------------------------------------------------------------------


@pytest.mark.unit
def test_no_velocity_means_no_forecast():
    assert forecast_completion(50, None)["sprints_needed"] is None


@pytest.mark.unit
def test_a_zero_velocity_does_not_forecast_zero_sprints():
    """"0 sprints" reads as "done", which is the opposite of what a zero velocity means
    — and dividing by it crashes."""
    out = forecast_completion(50, 0)
    assert out["sprints_needed"] is None
    assert "no rate to project from" in out["reason"]


@pytest.mark.unit
def test_it_rounds_up_because_a_part_sprint_is_a_sprint():
    assert forecast_completion(25, 10)["sprints_needed"] == 3


@pytest.mark.unit
def test_no_remaining_work_needs_no_sprints():
    assert forecast_completion(0, 10)["sprints_needed"] == 0


# -- baseline ------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("baseline", [None, {}, {"schedule": []}])
def test_no_baseline_means_no_slippage_figure(baseline):
    """Comparing the current plan to itself always reports zero slip — the most
    confidently wrong number this module could produce."""
    out = compare_to_baseline([{"name": "S1", "committed": 20}], baseline)
    assert out["comparable"] is False
    assert "no baseline" in out["reason"]


@pytest.mark.unit
def test_it_reports_what_moved_and_by_how_much():
    out = compare_to_baseline(
        [{"name": "S1", "committed": 25}, {"name": "S2", "committed": 10}],
        {"schedule": [{"name": "S1", "committed": 20}, {"name": "S2", "committed": 10}]},
    )
    assert out["change"] == 5
    assert out["sprints_changed"] == [
        {"sprint": "S1", "baseline": 20.0, "now": 25.0, "change": 5.0}
    ]


@pytest.mark.unit
def test_an_unchanged_plan_lists_nothing_as_moved():
    out = compare_to_baseline(
        [{"name": "S1", "committed": 20}],
        {"schedule": [{"name": "S1", "committed": 20}]},
    )
    assert out["comparable"] and out["sprints_changed"] == []


# -- risks ---------------------------------------------------------------------


@pytest.mark.unit
def test_a_risk_carries_the_size_of_what_it_threatens():
    out = assess_risks(
        [{"title": "Vendor API unstable", "threatens": ["a", "b"]}],
        [_sprint("S1", [_task("a", 5), _task("b", 8), _task("c", 3)])],
    )
    assert out["risks"][0]["exposure"] == 13
    assert out["total_exposure"] == 13


@pytest.mark.unit
def test_a_risk_naming_no_work_is_kept():
    """"The vendor contract is unsigned" threatens the project without pointing at a
    task. A register that silently discards those is one people stop filling in."""
    out = assess_risks([{"title": "Contract unsigned"}], [])
    assert out["risks"][0]["unlinked"] is True
    assert out["risks"][0]["title"] == "Contract unsigned"
    assert any("cannot be quantified" in n for n in out["notes"])


@pytest.mark.unit
def test_a_risk_pointing_at_work_not_in_the_plan_is_surfaced():
    """Usually a typo; sometimes a dependency on work nobody scheduled. Either is worth
    a person's attention."""
    out = assess_risks(
        [{"title": "Blocked", "threatens": ["ghost"]}],
        [_sprint("S1", [_task("a", 5)])],
    )
    assert any("not in the plan" in n and "ghost" in n for n in out["notes"])


@pytest.mark.unit
def test_the_risks_own_fields_survive():
    """Owner and impact are what a register is for; a function that returned only its
    own computed fields would throw the register away."""
    out = assess_risks([{"title": "X", "owner": "Ana", "impact": "high"}], [])
    assert out["risks"][0]["owner"] == "Ana"
    assert out["risks"][0]["impact"] == "high"


# -- the prompt ----------------------------------------------------------------


@pytest.mark.unit
def test_the_prompt_treats_a_null_as_an_answer():
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    p = " ".join(PM_SYS_MESSAGE.split())
    assert "A NULL IS AN ANSWER, NOT A GAP TO FILL" in p
    assert "Do NOT estimate the missing figure yourself" in p


@pytest.mark.unit
def test_the_prompt_warns_about_unrecognised_board_states():
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    p = " ".join(PM_SYS_MESSAGE.split())
    assert "unrecognised_states" in p
    assert "looks stalled when it is not" in p


@pytest.mark.unit
def test_the_reporting_tools_are_bound():
    from agents_orchestrator.pm_agent.agents.schedule import tools

    assert {"track_risks", "status_report"} <= {t.name for t in tools}


@pytest.mark.unit
def test_the_prompt_requires_labelling_invented_inputs():
    """Found by running it: asked for "two 20-point sprints" with no dates, the agent
    produced "Sprint 1 (2026-09-07 → 2026-09-20)" in the same list as genuine scheduler
    output. The scheduler returns empty dates when the caller gives none, so those were
    the model's own assumption presented in the tone of a computed result."""
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    p = " ".join(PM_SYS_MESSAGE.split())
    assert "LABEL ANYTHING YOU SUPPLIED YOURSELF" in p
    assert "a date you chose for one the plan is committed to" in p
