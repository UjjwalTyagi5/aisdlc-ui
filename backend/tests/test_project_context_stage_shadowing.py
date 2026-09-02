"""A later run must not hide an earlier stage's artifacts.

`_fetch_artifacts_for_project` took the project's single most recent Run and returned
its artifact columns wholesale. Stages run in sequence, so by the time anything
downstream asks for upstream context there is ALWAYS a newer run than the one holding
it — and every column on that newer run is NULL for the stages it did not perform.

The live symptom: a project whose Requirements had been baselined, and which then had
one Design chat, reported no requirements at all. The requirements had not gone
anywhere; the query stopped one row short of them. `read_project_requirements` returned
"This project has no requirements recorded yet" for a project with four.

This is the normal shape of a project, not an edge case, and it affected every caller of
build_context_for_project — the Development agent's upstream read included.

Each column now comes from the newest run that HAS one.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import context_broker  # noqa: E402

T0 = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)

# Real UUIDs: the function parses both before it reaches the session.
PROJECT = "f45e7d23-c821-44b3-a88b-6175f67ddef0"
TENANT = "81a736f4-cd44-4f63-842c-ae57023d0346"

REQS = {"board_project": "sdlc", "work_items": [{"title": "Password reset", "type": "User Story"}]}
DESIGN = {"hld": "an architecture"}


def _run(minutes, **columns):
    return SimpleNamespace(
        created_at=T0 + timedelta(minutes=minutes),
        requirements_payload=columns.get("requirements_payload"),
        design_artifacts=columns.get("design_artifacts"),
        development_artifacts=columns.get("development_artifacts"),
        testing_artifacts=columns.get("testing_artifacts"),
        code_review_artifacts=columns.get("code_review_artifacts"),
        security_artifacts=columns.get("security_artifacts"),
    )


def _fetch(runs, monkeypatch):
    """Drive the real column-picking logic over a fixed set of runs (newest first)."""
    captured = {}

    class _Result:
        @staticmethod
        def scalars():
            return SimpleNamespace(all=lambda: sorted(runs, key=lambda r: r.created_at, reverse=True))

    class _Db:
        async def execute(self, _stmt):
            captured["queried"] = True
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(context_broker, "_PROJECT_RUN_LOOKBACK", 100, raising=False)
    import shared.db as shared_db

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", lambda _t: _Db())
    return captured


@pytest.mark.unit
async def test_a_later_design_run_does_not_hide_the_requirements(monkeypatch):
    """THE BUG. Requirements ran first, a Design chat ran after, and the Design chat's
    run is the newest — with a NULL requirements_payload."""
    runs = [
        _run(0, requirements_payload=REQS),
        _run(60, design_artifacts=DESIGN),   # newer, and holds no requirements
    ]
    _fetch(runs, monkeypatch)

    out = await context_broker._fetch_artifacts_for_project(PROJECT, TENANT)
    assert out["requirements_payload"] == REQS
    assert out["design_artifacts"] == DESIGN


@pytest.mark.unit
async def test_the_newest_value_wins_when_a_stage_ran_twice(monkeypatch):
    """Re-running Requirements must supersede the earlier payload, not be ignored."""
    old = {"work_items": [{"title": "stale"}]}
    new = {"work_items": [{"title": "current"}]}
    _fetch([_run(0, requirements_payload=old), _run(90, requirements_payload=new)], monkeypatch)

    out = await context_broker._fetch_artifacts_for_project(PROJECT, TENANT)
    assert out["requirements_payload"] == new


@pytest.mark.unit
async def test_a_project_with_no_runs_returns_none(monkeypatch):
    """None means "no runs"; a dict of Nones would mean "runs, but nothing recorded"."""
    _fetch([], monkeypatch)
    assert await context_broker._fetch_artifacts_for_project(PROJECT, TENANT) is None


@pytest.mark.unit
async def test_a_stage_that_never_ran_is_none_not_missing(monkeypatch):
    """Callers index these keys directly."""
    _fetch([_run(0, requirements_payload=REQS)], monkeypatch)

    out = await context_broker._fetch_artifacts_for_project(PROJECT, TENANT)
    assert out["development_artifacts"] is None
    assert set(out) == set(context_broker._ARTIFACT_FIELDS)


@pytest.mark.unit
async def test_an_empty_payload_does_not_shadow_a_real_one(monkeypatch):
    """A run that recorded `{}` has nothing to say. Treating it as a value would hide
    the real payload behind an empty dict — the same bug in a subtler form."""
    _fetch([_run(0, requirements_payload=REQS), _run(30, requirements_payload={})], monkeypatch)

    out = await context_broker._fetch_artifacts_for_project(PROJECT, TENANT)
    assert out["requirements_payload"] == REQS


@pytest.mark.unit
async def test_every_stage_is_gathered_across_different_runs(monkeypatch):
    """The general case: one run per stage, each holding only its own output."""
    _fetch(
        [
            _run(0, requirements_payload=REQS),
            _run(30, design_artifacts=DESIGN),
            _run(60, development_artifacts={"prs": [1]}),
            _run(90, testing_artifacts={"suites": [2]}),
        ],
        monkeypatch,
    )

    out = await context_broker._fetch_artifacts_for_project(PROJECT, TENANT)
    assert out["requirements_payload"] == REQS
    assert out["design_artifacts"] == DESIGN
    assert out["development_artifacts"] == {"prs": [1]}
    assert out["testing_artifacts"] == {"suites": [2]}
