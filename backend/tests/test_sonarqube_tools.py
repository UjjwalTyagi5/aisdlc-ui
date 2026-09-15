"""SonarQube had thirteen connector operations and no way to reach any of them.

A project could grant SonarQube to its Security or Code Review stage, have it stored
and enforced, and watch nothing happen — the same gap Confluence had. These pin the
tools' shape and, more importantly, the two decisions in them: that a truncated issue
list says so, and that the administrative writes stay unexposed.
"""
from __future__ import annotations

import pytest

from shared.tools.sonarqube_quality import make_sonarqube_tools


def _tools():
    return {t.name: t for t in make_sonarqube_tools("security", "security")}


class _Conn:
    def __init__(self, read=None, write=None, raises=None):
        self._read, self._write, self._raises = read or {}, write or {}, raises
        self.calls = []

    async def read_adapter(self, op, **kw):
        self.calls.append((op, kw))
        if self._raises:
            raise self._raises
        return self._read.get(op)

    async def write_adapter(self, op, **kw):
        self.calls.append((op, kw))
        if self._raises:
            raise self._raises
        return self._write.get(op, {})


def _patch(monkeypatch, conn):
    import shared.tools.sonarqube_quality as mod

    async def _resolve(agent_id):
        return conn, ""

    monkeypatch.setattr(mod, "_resolve", _resolve)


@pytest.mark.unit
def test_the_quality_gate_is_the_first_tool_offered():
    """Order is the only steer a model gets about which to reach for, and a gate
    status answers 'can this ship' where an issue list does not."""
    assert make_sonarqube_tools("security", "security")[0].name == "check_quality_gate"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_passing_gate_says_so_plainly(monkeypatch):
    _patch(monkeypatch, _Conn(read={"get_quality_gate_status": {"status": "OK"}}))
    out = await _tools()["check_quality_gate"].ainvoke({"project": "acme"})
    assert "PASSES" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failing_gate_names_the_conditions(monkeypatch):
    """A failure with no reason attached is not actionable."""
    _patch(monkeypatch, _Conn(read={"get_quality_gate_status": {
        "status": "ERROR",
        "conditions": [
            {"metric": "coverage", "status": "ERROR", "actual": "61", "threshold": "80"},
            {"metric": "bugs", "status": "OK", "actual": "0", "threshold": "0"},
        ],
    }}))
    out = await _tools()["check_quality_gate"].ainvoke({"project": "acme"})
    assert "FAILS on 1 condition" in out
    assert "coverage" in out and "80" in out
    assert "bugs" not in out          # passing conditions are noise here


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_ok_gate_with_no_failing_condition_is_not_called_a_failure(monkeypatch):
    """A real SonarQube state — gate not computed, or nothing analysed yet. Reporting
    'fails' with nothing to show sends somebody hunting for a condition that is not
    there."""
    _patch(monkeypatch, _Conn(read={"get_quality_gate_status": {"status": "NONE", "conditions": []}}))
    out = await _tools()["check_quality_gate"].ainvoke({"project": "acme"})
    assert "FAILS" not in out
    assert "not have been analysed" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_truncated_issue_list_says_it_was_truncated(monkeypatch):
    """Otherwise a cut list reads as a short one, and 'only 50 issues' is a very
    different statement from 'the first 50 issues'."""
    issues = [
        {"severity": "MAJOR", "message": f"m{i}", "component": "c", "line": i, "key": f"k{i}"}
        for i in range(120)
    ]
    _patch(monkeypatch, _Conn(read={"list_issues": issues}))
    out = await _tools()["list_sonarqube_issues"].ainvoke({"project": "acme"})
    assert "70 more not shown" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_issues_is_stated_not_shown_as_an_empty_list(monkeypatch):
    _patch(monkeypatch, _Conn(read={"list_issues": []}))
    out = await _tools()["list_sonarqube_issues"].ainvoke({"project": "acme"})
    assert "no open SonarQube issues" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_tool_needing_a_project_refuses_without_one(monkeypatch):
    _patch(monkeypatch, _Conn())
    for name in ("check_quality_gate", "list_sonarqube_issues"):
        out = await _tools()[name].ainvoke({"project": ""})
        assert "which SonarQube project" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_connector_error_never_leaks_its_text(monkeypatch):
    """A SonarQube error can carry a token or a host."""
    _patch(monkeypatch, _Conn(raises=RuntimeError("token=sqp_deadbeef host=internal")))
    out = await _tools()["check_quality_gate"].ainvoke({"project": "acme"})
    assert "sqp_deadbeef" not in out and "internal" not in out
    assert "RuntimeError" in out


@pytest.mark.unit
def test_the_administrative_writes_are_not_exposed():
    """The connector can delete projects and set quality gates. Neither has an undo
    through this platform, so neither is a tool."""
    names = set(_tools())
    for dangerous in ("delete_project", "create_project", "set_quality_gate",
                      "set_issue_severity", "assign_issue"):
        assert not any(dangerous in n for n in names), dangerous


@pytest.mark.unit
def test_the_registry_agrees_with_the_factory_about_which_tools_write():
    """A write tool the registry thinks is a read would be bound to a read-only stage."""
    from shared.tools.stage_tools import _SPECS

    spec = _SPECS["sonarqube"]()
    names = {t.name for t in spec.factory("security", "security")}
    assert spec.write_tools <= names
    assert spec.write_tools == {"comment_on_sonarqube_issue", "transition_sonarqube_issue"}
