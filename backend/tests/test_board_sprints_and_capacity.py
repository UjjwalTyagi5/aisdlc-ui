"""Sprints and capacity — the two reads a schedule needs and neither provider had.

PHASE 0B OF THE PM AGENT. `list_sprints`, `team_capacity` and their equivalents did not
exist on either connector, so the timeline and resource-planning scopes had nothing to
read. Adding them exposes an asymmetry worth stating plainly rather than papering over:

    Azure DevOps  iterations AND capacity, both first-class APIs
    Jira          sprints via the Agile API; NO capacity API at all

Jira Software has no capacity concept. It lives in a plugin (Tempo, Structure) or in
people's calendars, so there is nothing to read without knowing which. The connector
declares it `not_supported` with that reason, the way `list_teams` already does — a
caller gets a clear refusal instead of a plausible zero.

BOTH ARE TEAM-SCOPED IN ADO. Iterations and capacity are team settings, so a project
with three teams has three answers and "the project's capacity" is not a thing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.ado_ingestion import _count_days_off  # noqa: E402


# -- days off ------------------------------------------------------------------


@pytest.mark.unit
def test_a_single_day_off_counts_as_one_day():
    """ADO returns start == end for one day. Subtracting (end - start) would call it
    zero and hand the day back to the plan — the off-by-one that silently overcommits
    everybody who takes a Friday."""
    assert _count_days_off([{"start": "2026-09-02T00:00:00Z", "end": "2026-09-02T00:00:00Z"}]) == 1


@pytest.mark.unit
def test_a_range_counts_both_ends():
    assert _count_days_off([{"start": "2026-09-01T00:00:00Z", "end": "2026-09-03T00:00:00Z"}]) == 3


@pytest.mark.unit
def test_several_ranges_add_up():
    assert _count_days_off([
        {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
        {"start": "2026-09-10T00:00:00Z", "end": "2026-09-10T00:00:00Z"},
    ]) == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    "ranges",
    [
        [],
        None,
        [{"start": "", "end": ""}],
        [{"start": "2026-09-01T00:00:00Z"}],          # no end
        [{"start": "not a date", "end": "also not"}],  # never raises into a capacity read
    ],
)
def test_unusable_ranges_are_skipped_rather_than_fatal(ranges):
    assert _count_days_off(ranges) == 0


# -- azure devops --------------------------------------------------------------


@pytest.mark.unit
async def test_ado_capacity_sums_a_persons_activities(monkeypatch):
    """capacityPerDay is PER ACTIVITY and one person can be listed against several
    (Development 4h, Testing 2h). Taking the first would understate anyone who splits
    their time."""
    from config import ado_ingestion

    payloads = {
        "capacities": {"value": [{
            "teamMember": {"id": "u1", "displayName": "Ana"},
            "activities": [
                {"name": "Development", "capacityPerDay": 4},
                {"name": "Testing", "capacityPerDay": 2},
            ],
            "daysOff": [],
        }]},
        "teamdaysoff": {"daysOff": []},
    }
    _install_client(monkeypatch, ado_ingestion, payloads)

    rows = await ado_ingestion.team_capacity(
        org_url="https://dev.azure.com/acme", project="P", team="T",
        iteration_id="it-1", pat="x",
    )
    assert rows[0]["capacity_per_day"] == 6
    assert [a["name"] for a in rows[0]["activities"]] == ["Development", "Testing"]


@pytest.mark.unit
async def test_ado_capacity_counts_personal_and_team_days_off(monkeypatch):
    """A capacity number that ignores leave is worse than none: it looks authoritative
    and overcommits the person. Public holidays come from a separate endpoint and apply
    to everybody."""
    from config import ado_ingestion

    payloads = {
        "capacities": {"value": [{
            "teamMember": {"id": "u1", "displayName": "Ana"},
            "activities": [{"name": "Dev", "capacityPerDay": 6}],
            "daysOff": [{"start": "2026-09-02T00:00:00Z", "end": "2026-09-02T00:00:00Z"}],
        }]},
        "teamdaysoff": {"daysOff": [
            {"start": "2026-09-07T00:00:00Z", "end": "2026-09-08T00:00:00Z"}
        ]},
    }
    _install_client(monkeypatch, ado_ingestion, payloads)

    rows = await ado_ingestion.team_capacity(
        org_url="https://dev.azure.com/acme", project="P", team="T",
        iteration_id="it-1", pat="x",
    )
    assert rows[0]["days_off"] == 3      # 1 personal + 2 team


@pytest.mark.unit
async def test_a_team_with_no_days_off_record_is_not_an_error(monkeypatch):
    """That endpoint 404s for a team that has never set one. Failing the whole capacity
    read over an absent holiday list would be absurd."""
    import httpx

    from config import ado_ingestion

    payloads = {
        "capacities": {"value": [{
            "teamMember": {"id": "u1", "displayName": "Ana"},
            "activities": [{"name": "Dev", "capacityPerDay": 6}],
            "daysOff": [],
        }]},
        "teamdaysoff": httpx.HTTPError("404"),
    }
    _install_client(monkeypatch, ado_ingestion, payloads)

    rows = await ado_ingestion.team_capacity(
        org_url="https://dev.azure.com/acme", project="P", team="T",
        iteration_id="it-1", pat="x",
    )
    assert rows[0]["days_off"] == 0


@pytest.mark.unit
async def test_ado_iterations_keep_azures_own_time_frame(monkeypatch):
    """Deriving past/current/future from the dates would mean re-implementing ADO's
    notion of "current" and getting the boundary wrong on the changeover day."""
    from config import ado_ingestion

    payloads = {"iterations": {"value": [{
        "id": "it-1", "name": "Sprint 3", "path": "P\\\\Sprint 3",
        "attributes": {
            "startDate": "2026-09-01T00:00:00Z",
            "finishDate": "2026-09-14T00:00:00Z",
            "timeFrame": "current",
        },
    }]}}
    _install_client(monkeypatch, ado_ingestion, payloads)

    out = await ado_ingestion.list_iterations(
        org_url="https://dev.azure.com/acme", project="P", team="T", pat="x"
    )
    assert out[0]["name"] == "Sprint 3"
    assert out[0]["time_frame"] == "current"
    assert out[0]["start_date"].startswith("2026-09-01")


# -- jira ----------------------------------------------------------------------


@pytest.mark.unit
def test_jira_declares_capacity_unsupported_with_a_reason():
    """A plausible zero would be worse than a refusal: a planner would schedule against
    it. `list_teams` already sets this precedent."""
    from config.connectors.jira import JiraConnector

    cap = JiraConnector("https://x.atlassian.net").capability_manifest() \
        .read_capabilities["team_capacity"]
    assert cap.status == "not_supported"
    assert "no capacity API" in cap.description
    assert "Tempo" in cap.description


@pytest.mark.unit
def test_jira_declares_sprints_implemented():
    from config.connectors.jira import JiraConnector

    cap = JiraConnector("https://x.atlassian.net").capability_manifest() \
        .read_capabilities["list_sprints"]
    assert cap.status == "implemented"


@pytest.mark.unit
async def test_jira_sprint_states_are_mapped_to_the_shared_vocabulary(monkeypatch):
    """Jira says active/future/closed and ADO says current/future/past. A caller should
    read one set of values whichever board it is talking to."""
    from config.connectors.jira import JiraConnector

    c = JiraConnector("https://x.atlassian.net")
    calls = []

    async def _req(method, path, tenant_id="", **kw):
        calls.append(path)
        if "board?" in path:
            return {"values": [{"id": 9}]}, 0
        return {"values": [
            {"id": 1, "name": "Sprint 1", "state": "closed"},
            {"id": 2, "name": "Sprint 2", "state": "active"},
            {"id": 3, "name": "Sprint 3", "state": "future"},
        ]}, 0

    monkeypatch.setattr(c, "_jira_request_with_retry", _req)
    out = await c.list_sprints("SCRUM")

    assert [s["time_frame"] for s in out] == ["past", "current", "future"]
    assert "board?projectKeyOrId=SCRUM" in calls[0]


@pytest.mark.unit
async def test_a_project_with_no_board_yields_no_sprints(monkeypatch):
    """A Kanban-only project has no sprints. Empty is the answer; raising would make the
    planner treat a legitimate setup as a failure."""
    from config.connectors.jira import JiraConnector

    c = JiraConnector("https://x.atlassian.net")

    async def _req(method, path, tenant_id="", **kw):
        return {"values": []}, 0

    monkeypatch.setattr(c, "_jira_request_with_retry", _req)
    assert await c.list_sprints("KANBAN") == []


# -- both providers answer the same shape --------------------------------------


@pytest.mark.unit
def test_both_providers_expose_sprints_through_the_same_operation():
    """The agent calls read_adapter("list_sprints", ...) and must not branch on which
    board is connected."""
    import inspect

    from config.connectors.azure_devops import AzureDevOpsConnector
    from config.connectors.jira import JiraConnector

    for cls in (AzureDevOpsConnector, JiraConnector):
        assert "list_sprints" in inspect.getsource(cls.read_adapter), cls.__name__


def _install_client(monkeypatch, module, payloads):
    """A fake httpx.AsyncClient that answers by URL fragment.

    A value that is an exception is RAISED rather than returned, so a test can say "this
    endpoint 404s" without a second mechanism.
    """
    class _Resp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, **_kw):
            for fragment, payload in payloads.items():
                if fragment in url:
                    if isinstance(payload, Exception):
                        raise payload
                    return _Resp(payload)
            return _Resp({"value": []})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kw: _Client())
