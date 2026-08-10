"""Phase 11.4 — output parsers for shell-capable skills.

Each parser ships with a happy-path test (parses fixture JSON correctly) +
a malformed-input test (returns None / empty list, never raises). Coverage
matches the spec's "Tests: each parser ships with a happy-path test + a
malformed-input test" requirement.
"""
from __future__ import annotations

import json

import pytest

from agents_orchestrator.testing_agent.tools.skill_parsers import (
    get_parser,
    list_parsers,
    parse_bandit_json,
    parse_mutmut_summary,
    parse_npm_audit_json,
    parse_pip_audit_json,
    parse_stryker_json,
)


# --- Registry --------------------------------------------------------------

def test_registry_lists_all_5_parsers():
    names = list_parsers()
    for expected in ("stryker_json", "mutmut_summary", "bandit_json",
                     "pip_audit_json", "npm_audit_json"):
        assert expected in names


def test_get_parser_returns_callable_for_known_names():
    p = get_parser("stryker_json")
    assert callable(p)


def test_get_parser_returns_none_for_unknown():
    assert get_parser("not_a_real_parser") is None


# --- Stryker ---------------------------------------------------------------

STRYKER_FIXTURE = {
    "files": {
        "src/a.cs": {
            "mutants": [
                {"status": "Killed", "mutatorName": "BinaryOp", "originalCode": "+", "mutatedCode": "-",
                 "location": {"start": {"line": 10}}},
                {"status": "Killed", "mutatorName": "BinaryOp", "originalCode": "*", "mutatedCode": "/",
                 "location": {"start": {"line": 12}}},
                {"status": "Survived", "mutatorName": "Logical", "originalCode": "&&", "mutatedCode": "||",
                 "location": {"start": {"line": 15}}},
                {"status": "NoCoverage", "mutatorName": "BinaryOp",
                 "location": {"start": {"line": 20}}},
            ]
        },
        "src/b.cs": {
            "mutants": [
                {"status": "Killed", "mutatorName": "Boolean", "originalCode": "true", "mutatedCode": "false",
                 "location": {"start": {"line": 3}}},
                {"status": "Survived", "mutatorName": "Conditional", "originalCode": ">", "mutatedCode": ">=",
                 "location": {"start": {"line": 7}}},
            ]
        },
    }
}


def test_parse_stryker_json_happy(tmp_path):
    p = tmp_path / "stryker.json"
    p.write_text(json.dumps(STRYKER_FIXTURE))
    result = parse_stryker_json(str(p))
    assert result is not None
    assert result["tool"] == "stryker"
    assert result["mutants_total"] == 6
    assert result["mutants_killed"] == 3
    assert result["mutants_survived"] == 2
    assert result["mutants_no_coverage"] == 1
    # Kill rate = 3 / (3 + 2) * 100 = 60%
    assert result["kill_rate_pct"] == 60.0
    # Top survivors captured
    assert len(result["top_survivors"]) == 2
    survivor_files = {s["file"] for s in result["top_survivors"]}
    assert survivor_files == {"src/a.cs", "src/b.cs"}


def test_parse_stryker_json_missing_file():
    assert parse_stryker_json("/nope/x.json") is None


def test_parse_stryker_json_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not valid json {{{")
    assert parse_stryker_json(str(bad)) is None


def test_parse_stryker_json_empty_files(tmp_path):
    """Stryker ran but found no mutants — kill_rate=0, total=0, doesn't crash."""
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"files": {}}))
    result = parse_stryker_json(str(p))
    assert result["mutants_total"] == 0
    assert result["kill_rate_pct"] == 0.0


# --- mutmut ----------------------------------------------------------------

def test_parse_mutmut_summary_happy(tmp_path):
    p = tmp_path / "mutmut.json"
    p.write_text(json.dumps({"killed": 8, "survived": 2, "timeout": 1, "suspicious": 0}))
    result = parse_mutmut_summary(str(p))
    assert result["tool"] == "mutmut"
    assert result["mutants_killed"] == 8
    assert result["mutants_survived"] == 2
    assert result["mutants_timeout"] == 1
    # Kill rate = 8 / (8+2) * 100 = 80%
    assert result["kill_rate_pct"] == 80.0


def test_parse_mutmut_summary_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    assert parse_mutmut_summary(str(p)) is None


# --- Bandit ----------------------------------------------------------------

BANDIT_FIXTURE = {
    "results": [
        {
            "filename": "app/views.py",
            "line_number": 42,
            "issue_severity": "HIGH",
            "issue_text": "Possible hardcoded password.",
            "test_id": "B105",
            "issue_cwe": {"id": 798},
        },
        {
            "filename": "app/db.py",
            "line_number": 100,
            "issue_severity": "MEDIUM",
            "issue_text": "Possible SQL injection.",
            "test_id": "B608",
            "issue_cwe": {"id": 89},
        },
    ]
}


def test_parse_bandit_json_happy(tmp_path):
    p = tmp_path / "bandit.json"
    p.write_text(json.dumps(BANDIT_FIXTURE))
    result = parse_bandit_json(str(p))
    assert isinstance(result, list)
    assert len(result) == 2
    high = next(r for r in result if r["severity"] == "HIGH")
    assert high["rule_id"] == "B105"
    assert high["file"] == "app/views.py"
    assert high["cwe"] == "CWE-798"


def test_parse_bandit_json_no_results(tmp_path):
    p = tmp_path / "clean.json"
    p.write_text(json.dumps({"results": []}))
    assert parse_bandit_json(str(p)) == []


def test_parse_bandit_json_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("oops")
    assert parse_bandit_json(str(p)) is None


# --- pip-audit -------------------------------------------------------------

PIP_AUDIT_FIXTURE = {
    "dependencies": [
        {
            "name": "requests",
            "version": "2.20.0",
            "vulns": [
                {
                    "id": "CVE-2018-18074",
                    "fix_versions": ["2.20.1"],
                    "description": "Requests Library before 2.20.0 leaks credentials.",
                    "severity": "HIGH",
                    "link": "https://nvd.nist.gov/vuln/detail/CVE-2018-18074",
                }
            ]
        },
        {
            "name": "urllib3",
            "version": "1.24.1",
            "vulns": []
        },
    ]
}


def test_parse_pip_audit_json_happy(tmp_path):
    p = tmp_path / "pip-audit.json"
    p.write_text(json.dumps(PIP_AUDIT_FIXTURE))
    result = parse_pip_audit_json(str(p))
    assert isinstance(result, list)
    # Only the package with vulns shows up
    assert len(result) == 1
    v = result[0]
    assert v["package"] == "requests"
    assert v["installed_version"] == "2.20.0"
    assert v["severity"] == "HIGH"
    assert v["cve"] == "CVE-2018-18074"
    assert "2.20.1" in v["fix_versions"]


def test_parse_pip_audit_json_no_vulns(tmp_path):
    p = tmp_path / "clean.json"
    p.write_text(json.dumps({"dependencies": [{"name": "x", "version": "1.0", "vulns": []}]}))
    assert parse_pip_audit_json(str(p)) == []


def test_parse_pip_audit_json_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("nope")
    assert parse_pip_audit_json(str(p)) is None


# --- npm audit -------------------------------------------------------------

NPM_AUDIT_FIXTURE = {
    "vulnerabilities": {
        "lodash": {
            "severity": "high",
            "via": [{"source": "GHSA-jf85-cpcp-j695", "title": "Prototype Pollution"}],
            "range": "<4.17.21",
            "fixAvailable": {"version": "4.17.21"},
            "title": "Prototype Pollution in lodash",
        },
        "minimist": {
            "severity": "moderate",
            "via": [{"source": "GHSA-vh95-rmgr-6w4m", "title": "Prototype Pollution"}],
            "range": "<1.2.6",
            "fixAvailable": False,
        },
    }
}


def test_parse_npm_audit_json_happy(tmp_path):
    p = tmp_path / "npm-audit.json"
    p.write_text(json.dumps(NPM_AUDIT_FIXTURE))
    result = parse_npm_audit_json(str(p))
    assert isinstance(result, list)
    assert len(result) == 2
    by_pkg = {v["package"]: v for v in result}
    assert by_pkg["lodash"]["severity"] == "HIGH"
    assert "GHSA-jf85-cpcp-j695" in (by_pkg["lodash"]["cve"] or "")
    assert "4.17.21" in by_pkg["lodash"]["fix_versions"]


def test_parse_npm_audit_json_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json {{{")
    assert parse_npm_audit_json(str(p)) is None
