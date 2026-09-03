"""The canonical board item carries what a schedule is built from.

PHASE 0 OF THE PM AGENT. `make_board_item` described WHAT a piece of work is — title,
type, state, assignee — and nothing about how big it is, when it is due, or which sprint
it belongs to. Those are the first questions a project manager asks, so timeline and
resource planning could not be built on it at all.

Both providers already fetched the underlying values. ADO in particular read
`System.IterationPath` off every work item and then dropped it on the way through the
canonical mapper.

NONE IS NOT ZERO, and that distinction is the reason several of these tests exist. An
unestimated item and a zero-point item are different facts; averaging the second into a
velocity is how a plan quietly lies about how much a team gets through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.ado_ingestion import _first_number  # noqa: E402
from config.connectors.jira import _jira_planning  # noqa: E402
from config.connectors.models import make_board_item  # noqa: E402


# -- the canonical shape -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    ["estimate", "iteration", "start_date", "due_date", "remaining_work",
     "completed_work", "priority"],
)
def test_every_planning_field_is_present_even_when_unknown(field):
    """A caller reads these keys directly; a missing key is a KeyError, not a gap."""
    item = make_board_item(provider_kind="jira", item_id="1", title="x")
    assert field in item


@pytest.mark.unit
def test_an_unestimated_item_is_none_not_zero():
    item = make_board_item(provider_kind="jira", item_id="1", title="x")
    assert item["estimate"] is None
    assert item["remaining_work"] is None


@pytest.mark.unit
def test_a_zero_point_item_keeps_its_zero():
    """The other half of the same rule: 0 is a real estimate somebody entered."""
    item = make_board_item(provider_kind="jira", item_id="1", title="x", estimate=0)
    assert item["estimate"] == 0
    assert item["estimate"] is not None


@pytest.mark.unit
def test_nothing_existing_was_displaced():
    """The requirements ingestion and the story synthesiser read these keys."""
    item = make_board_item(
        provider_kind="azure_devops", item_id="42", title="Login",
        item_type="User Story", state="Active", estimate=5,
    )
    assert item["source_key"] == "42"
    assert item["work_item_type"] == "User Story"
    assert item["work_item_id"] == "42"


# -- azure devops --------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((8,), 8.0),
        ((None, 13), 13.0),                 # Epics carry Effort, not StoryPoints
        ((None, None, "3.5"), 3.5),         # numeric strings are still numbers
        ((None, None, None), None),
        (("", None), None),
        (("not a number",), None),          # never raises into an ingestion run
        ((0,), 0.0),                        # a real zero survives
    ],
)
def test_ado_takes_the_first_field_that_holds_a_number(values, expected):
    """Stories carry StoryPoints and Epics carry Effort. Reading only one would estimate
    half the backlog at nothing."""
    assert _first_number(*values) == expected


# -- jira ----------------------------------------------------------------------


@pytest.mark.unit
def test_jira_finds_story_points_in_the_usual_custom_field():
    assert _jira_planning({"customfield_10016": 5})["estimate"] == 5.0


@pytest.mark.unit
@pytest.mark.parametrize("field", ["customfield_10016", "customfield_10026", "customfield_10002"])
def test_jira_tries_each_known_story_point_field(field):
    """There is NO fixed field id for story points — it differs per Jira site. Reading
    only one id would silently yield no estimates on a site using another."""
    assert _jira_planning({field: 8})["estimate"] == 8.0


@pytest.mark.unit
def test_an_unknown_story_point_field_yields_no_estimate_rather_than_a_wrong_one():
    assert _jira_planning({"customfield_99999": 8})["estimate"] is None


@pytest.mark.unit
def test_jira_falls_back_to_the_time_estimate_in_hours():
    """`timeoriginalestimate` is in SECONDS. Passing it through raw would put 28800 into
    a field the rest of the system reads as points or hours."""
    assert _jira_planning({"timeoriginalestimate": 28800})["estimate"] == 8.0


@pytest.mark.unit
def test_story_points_win_over_the_time_estimate():
    out = _jira_planning({"customfield_10016": 3, "timeoriginalestimate": 28800})
    assert out["estimate"] == 3.0


@pytest.mark.unit
def test_remaining_and_spent_are_converted_to_hours_too():
    out = _jira_planning({"timeestimate": 7200, "timespent": 3600})
    assert out["remaining_work"] == 2.0
    assert out["completed_work"] == 1.0


@pytest.mark.unit
def test_the_current_sprint_is_the_last_one_listed():
    """Jira APPENDS on each move and keeps the history, so the last entry is where the
    item is now — the first is where it started."""
    out = _jira_planning({
        "customfield_10020": [
            {"name": "Sprint 1"}, {"name": "Sprint 2"}, {"name": "Sprint 3"},
        ]
    })
    assert out["iteration"] == "Sprint 3"


@pytest.mark.unit
def test_a_sprint_returned_as_a_string_is_still_parsed():
    """Older Jira returns "...,name=Sprint 3,startDate=..." rather than an object."""
    out = _jira_planning({
        "customfield_10020": ["com.atlassian.greenhopper.Sprint@1[id=5,name=Sprint 3,state=ACTIVE]"]
    })
    assert out["iteration"] == "Sprint 3"


@pytest.mark.unit
@pytest.mark.parametrize("sprints", [[], None, "not a list"])
def test_no_sprint_is_an_empty_string_not_a_crash(sprints):
    assert _jira_planning({"customfield_10020": sprints})["iteration"] == ""


@pytest.mark.unit
def test_the_due_date_comes_from_jiras_own_field():
    assert _jira_planning({"duedate": "2026-09-30"})["due_date"] == "2026-09-30"


@pytest.mark.unit
def test_an_empty_issue_yields_every_key_with_nothing_in_it():
    out = _jira_planning({})
    assert set(out) == {
        "estimate", "iteration", "start_date", "due_date",
        "remaining_work", "completed_work",
    }
    assert out["estimate"] is None and out["iteration"] == ""
