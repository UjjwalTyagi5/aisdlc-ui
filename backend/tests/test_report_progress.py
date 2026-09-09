""""How much is done and how much is left", answered without inventing anything.

THE EASY WAY TO BUILD THIS IS WRONG. A progress tool that decides "done" by looking at
the state NAME works on a demo board and misreports every real one: teams rename states
freely, and "Ready for UAT", "Signed off" or "Won't fix" mean nothing to a keyword match.
Azure DevOps and Jira both publish a CATEGORY per workflow state, so the board is asked
rather than guessed at.

WHAT THESE TESTS GUARD is the arithmetic around the awkward cases, because that is where
a progress figure turns into a lie:

  · an item whose state has no category must not be silently counted as done OR as
    outstanding — it moves the percentage either way;
  · removed work is neither delivered nor left to do, and leaving it in the denominator
    understates completion forever;
  · a points percentage over a half-estimated board sounds precise and is not.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.pm_agent.agents import schedule  # noqa: E402


class _Board:
    """A board with a real state catalogue, in the shape the connectors return."""

    def __init__(self, items, states, states_raise=False):
        self._items, self._states, self._raise = items, states, states_raise

    async def read_adapter(self, op, **kw):
        if op == "list_all_items":
            return self._items
        if op == "list_states":
            if self._raise:
                raise RuntimeError("catalogue unavailable")
            return self._states
        raise AssertionError(op)


def _item(state, *, kind="User Story", estimate=None):
    return {"work_item_type": kind, "state": state, "estimate": estimate}


#: Azure DevOps' own vocabulary.
ADO_STATES = [
    {"name": "New", "category": "Proposed"},
    {"name": "Active", "category": "InProgress"},
    {"name": "Ready for UAT", "category": "InProgress"},
    {"name": "Closed", "category": "Completed"},
    {"name": "Removed", "category": "Removed"},
]


async def _run(monkeypatch, items, states=ADO_STATES, states_raise=False):
    board = _Board(items, states, states_raise)

    async def _conn(mode="read", provider=""):
        return board, None

    monkeypatch.setattr(
        "agents_orchestrator.requirements_agent.agents.planning._board_connector", _conn)

    async def _proj():
        return "Payments"

    monkeypatch.setattr(schedule, "_board_project", _proj)
    return json.loads(await schedule.report_progress.ainvoke({}))


@pytest.mark.asyncio
async def test_it_reads_the_boards_category_not_the_state_name(monkeypatch):
    """THE HEADLINE. "Ready for UAT" is in progress because the BOARD says its category
    is InProgress. No keyword match would know that, and a team that renames its states
    is the normal case rather than the exception."""
    out = await _run(monkeypatch, [
        _item("Closed"), _item("Ready for UAT"), _item("New"),
    ])

    assert out["buckets"]["done"]["count"] == 1
    assert out["buckets"]["in_progress"]["count"] == 1
    assert out["buckets"]["not_started"]["count"] == 1
    assert out["by_count"]["done_pct"] == pytest.approx(33.3, abs=0.1)


@pytest.mark.asyncio
async def test_removed_work_leaves_the_denominator(monkeypatch):
    """Cancelled work is neither delivered nor outstanding. Counting it as either moves
    the figure: as done it flatters, as left-to-do it condemns the team forever."""
    out = await _run(monkeypatch, [_item("Closed"), _item("Removed")])

    assert out["by_count"]["counted"] == 1
    assert out["by_count"]["done_pct"] == 100.0
    assert out["buckets"]["removed"]["count"] == 1


@pytest.mark.asyncio
async def test_an_uncategorised_state_is_excluded_and_announced(monkeypatch):
    """THE QUIET FAILURE. A state the catalogue does not describe cannot be classified,
    and folding it either way states something the board never said. It is excluded and
    the caller is TOLD, so the agent can say so rather than quoting a clean-looking
    number over twelve items it could not read."""
    out = await _run(monkeypatch, [
        _item("Closed"), _item("Some Custom State"),
    ])

    assert out["buckets"]["unknown"]["count"] == 1
    assert out["by_count"]["counted"] == 1
    assert "unknown_note" in out
    assert "excluded" in out["unknown_note"]


@pytest.mark.asyncio
async def test_a_broken_catalogue_does_not_become_a_confident_answer(monkeypatch):
    """If the state catalogue cannot be read at all, everything is unknown — and the
    tool says why rather than reporting 0% done, which would read as "nothing is
    finished" instead of "I could not tell"."""
    out = await _run(monkeypatch, [_item("Closed"), _item("New")], states_raise=True)

    assert out["buckets"]["unknown"]["count"] == 2
    assert out["by_count"]["counted"] == 0
    assert "catalogue_note" in out


@pytest.mark.asyncio
async def test_both_measures_are_reported_and_coverage_is_stated(monkeypatch):
    """Count and points disagree, and the points figure only describes the estimated
    part of the board. Quoting it alone over a half-estimated backlog is how a plan
    claims 80% while most of the work is unmeasured."""
    out = await _run(monkeypatch, [
        _item("Closed", estimate=8), _item("New", estimate=2), _item("New"),
    ])

    assert out["by_count"]["done_pct"] == pytest.approx(33.3, abs=0.1)
    assert out["by_estimate"]["done_pct"] == 80.0
    assert out["by_estimate"]["items_with_an_estimate"] == 2
    assert out["by_estimate"]["items_without_one"] == 1


@pytest.mark.asyncio
async def test_an_unestimated_board_says_so_instead_of_reporting_zero(monkeypatch):
    """NON-VACUITY on the points path: no estimates means no points figure, not 0%."""
    out = await _run(monkeypatch, [_item("Closed"), _item("New")])

    assert "done_pct" not in out["by_estimate"]
    assert "no item carries an estimate" in out["by_estimate"]["note"]


@pytest.mark.asyncio
async def test_an_empty_board_is_a_sentence_not_a_zero(monkeypatch):
    """0% done on an empty backlog is technically true and useless; it reads as a team
    that has achieved nothing rather than a board nobody has filled in."""
    board = _Board([], ADO_STATES)

    async def _conn(mode="read", provider=""):
        return board, None

    monkeypatch.setattr(
        "agents_orchestrator.requirements_agent.agents.planning._board_connector", _conn)

    async def _proj():
        return "Payments"

    monkeypatch.setattr(schedule, "_board_project", _proj)
    out = await schedule.report_progress.ainvoke({})

    assert "no work items" in out
    assert "%" not in out


@pytest.mark.asyncio
async def test_the_raw_state_counts_are_kept(monkeypatch):
    """The buckets are an interpretation; the per-state counts are the fact underneath.
    Keeping both lets somebody check the summary rather than take it on trust."""
    out = await _run(monkeypatch, [_item("Closed"), _item("Closed"), _item("New")])

    assert out["by_state"]["Closed"] == 2
    assert out["by_state"]["New"] == 1


def test_the_prompt_tells_the_agent_not_to_absorb_the_awkward_numbers():
    """The tool returning `unknown_note` is only half of it — a prompt that says
    "report progress" with no caveat is where a confident, wrong percentage comes from."""
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    assert "report_progress" in PM_SYS_MESSAGE
    assert "DONE ON THE BOARD IS NOT DELIVERED" in PM_SYS_MESSAGE
    assert "unknown" in PM_SYS_MESSAGE
