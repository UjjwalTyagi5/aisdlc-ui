"""Tests for SonarQubeConnector — mirrors test_m6_confluence_connector.py's shape.

Asserts capability_manifest() declares BOTH read and write capabilities, and that
each CRUD method hits the expected SonarQube Web API path via respx-mocked HTTP
calls, authenticating with HTTP Basic (token as username, empty password).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import respx

from config.connectors.sonarqube import SonarQubeConnector

SONAR_BASE = "https://sonar.test.internal"
_PROJECT_FIXTURE = {"components": [{"key": "my-app", "name": "My App"}]}
_QUALITY_GATE_FIXTURE = {
    "projectStatus": {
        "status": "ERROR",
        "conditions": [
            {"metricKey": "coverage", "status": "ERROR", "actualValue": "42.0", "errorThreshold": "80"}
        ],
    }
}
_MEASURES_FIXTURE = {
    "component": {
        "measures": [
            {"metric": "coverage", "value": "42.0"},
            {"metric": "bugs", "value": "3"},
        ]
    }
}
_ISSUE_ROW = {
    "key": "AXabc123",
    "rule": "python:S1481",
    "severity": "MAJOR",
    "status": "OPEN",
    "type": "CODE_SMELL",
    "message": "Remove unused variable",
    "component": "my-app:src/app.py",
    "line": 10,
}
_ISSUES_FIXTURE = {"issues": [_ISSUE_ROW]}
_QUALITY_GATES_LIST_FIXTURE = {
    "qualitygates": [{"id": "1", "name": "Sonar way", "isDefault": True}]
}


@pytest.fixture(autouse=True)
def _isolate_sonarqube_env(monkeypatch):
    """Keep these tests hermetic regardless of a developer's local .env."""
    import config.connectors.sonarqube as _sq_mod
    monkeypatch.setattr(_sq_mod, "SONARQUBE_URL", "")
    monkeypatch.setattr(_sq_mod, "SONARQUBE_TOKEN", "")


def _make_connector() -> SonarQubeConnector:
    return SonarQubeConnector(SONAR_BASE)


@pytest.mark.unit
def test_sonarqube_connector_instantiable():
    connector = _make_connector()
    assert connector is not None
    assert connector.connector_name == "sonarqube"


@pytest.mark.unit
def test_capability_manifest_declares_read_and_write():
    manifest = _make_connector().capability_manifest()
    assert manifest.read_capabilities, "expected at least one read capability"
    assert manifest.write_capabilities, "expected at least one write capability"
    assert "list_issues" in manifest.read_capabilities
    assert "get_quality_gate_status" in manifest.read_capabilities
    assert "transition_issue" in manifest.write_capabilities
    assert "create_project" in manifest.write_capabilities


@pytest.mark.unit
@respx.mock
async def test_list_projects_returns_picker_shape():
    respx.get(f"{SONAR_BASE}/api/projects/search").mock(
        return_value=httpx.Response(200, json=_PROJECT_FIXTURE)
    )
    connector = _make_connector()
    projects = await connector.list_projects()
    assert projects == [{"name": "My App", "key": "my-app"}]


@pytest.mark.unit
@respx.mock
async def test_get_quality_gate_status_returns_conditions():
    respx.get(f"{SONAR_BASE}/api/qualitygates/project_status").mock(
        return_value=httpx.Response(200, json=_QUALITY_GATE_FIXTURE)
    )
    connector = _make_connector()
    status = await connector.get_quality_gate_status("my-app")
    assert status["status"] == "ERROR"
    assert status["conditions"][0]["metric"] == "coverage"


@pytest.mark.unit
@respx.mock
async def test_get_measures_returns_metric_value_map():
    respx.get(f"{SONAR_BASE}/api/measures/component").mock(
        return_value=httpx.Response(200, json=_MEASURES_FIXTURE)
    )
    connector = _make_connector()
    measures = await connector.get_measures("my-app")
    assert measures == {"coverage": "42.0", "bugs": "3"}


@pytest.mark.unit
@respx.mock
async def test_list_issues_returns_canonical_shape():
    respx.get(f"{SONAR_BASE}/api/issues/search").mock(
        return_value=httpx.Response(200, json=_ISSUES_FIXTURE)
    )
    connector = _make_connector()
    issues = await connector.list_issues("my-app")
    assert len(issues) == 1
    assert issues[0]["key"] == "AXabc123"
    assert issues[0]["severity"] == "MAJOR"


@pytest.mark.unit
@respx.mock
async def test_fetch_issue_detail_returns_single_issue():
    respx.get(f"{SONAR_BASE}/api/issues/search").mock(
        return_value=httpx.Response(200, json=_ISSUES_FIXTURE)
    )
    connector = _make_connector()
    issue = await connector.fetch_issue_detail(issue_key="AXabc123")
    assert issue["key"] == "AXabc123"


@pytest.mark.unit
@respx.mock
async def test_fetch_issue_detail_raises_when_not_found():
    respx.get(f"{SONAR_BASE}/api/issues/search").mock(
        return_value=httpx.Response(200, json={"issues": []})
    )
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.fetch_issue_detail(issue_key="missing")


@pytest.mark.unit
@respx.mock
async def test_list_quality_gates():
    respx.get(f"{SONAR_BASE}/api/qualitygates/list").mock(
        return_value=httpx.Response(200, json=_QUALITY_GATES_LIST_FIXTURE)
    )
    connector = _make_connector()
    gates = await connector.list_quality_gates()
    assert gates == [{"id": "1", "name": "Sonar way", "isDefault": True}]


@pytest.mark.unit
@respx.mock
async def test_create_project_derives_key_when_absent():
    route = respx.post(f"{SONAR_BASE}/api/projects/create").mock(
        return_value=httpx.Response(200, json={"project": {"key": "my-new-app", "name": "My New App"}})
    )
    connector = _make_connector()
    result = await connector.create_project("My New App")
    assert result == {"key": "my-new-app", "name": "My New App"}
    sent_params = dict(route.calls.last.request.url.params)
    assert sent_params["project"] == "my-new-app"


@pytest.mark.unit
@respx.mock
async def test_delete_project():
    respx.post(f"{SONAR_BASE}/api/projects/delete").mock(return_value=httpx.Response(204))
    connector = _make_connector()
    result = await connector.delete_project("my-app")
    assert result == {"project": "my-app", "deleted": True}


@pytest.mark.unit
@respx.mock
async def test_add_comment():
    respx.post(f"{SONAR_BASE}/api/issues/add_comment").mock(
        return_value=httpx.Response(200, json={"issue": _ISSUE_ROW})
    )
    connector = _make_connector()
    result = await connector.add_comment("AXabc123", "please fix")
    assert result["key"] == "AXabc123"


@pytest.mark.unit
@respx.mock
async def test_transition_issue_accepts_known_transition():
    respx.post(f"{SONAR_BASE}/api/issues/do_transition").mock(
        return_value=httpx.Response(200, json={"issue": {**_ISSUE_ROW, "status": "RESOLVED"}})
    )
    connector = _make_connector()
    result = await connector.transition_issue(issue_key="AXabc123", target_state="Resolve")
    assert result["status"] == "RESOLVED"


@pytest.mark.unit
async def test_transition_issue_rejects_unknown_transition():
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.transition_issue(issue_key="AXabc123", target_state="not-a-real-transition")


@pytest.mark.unit
@respx.mock
async def test_assign_issue():
    respx.post(f"{SONAR_BASE}/api/issues/assign").mock(
        return_value=httpx.Response(200, json={"issue": {**_ISSUE_ROW, "assignee": "dev1"}})
    )
    connector = _make_connector()
    result = await connector.assign_issue("AXabc123", assignee="dev1")
    assert result["assignee"] == "dev1"


@pytest.mark.unit
@respx.mock
async def test_set_quality_gate():
    respx.post(f"{SONAR_BASE}/api/qualitygates/select").mock(return_value=httpx.Response(204))
    connector = _make_connector()
    result = await connector.set_quality_gate("my-app", "Sonar way")
    assert result == {"project": "my-app", "gate": "Sonar way"}


@pytest.mark.unit
@respx.mock
async def test_read_adapter_dispatches_list_issues():
    respx.get(f"{SONAR_BASE}/api/issues/search").mock(
        return_value=httpx.Response(200, json=_ISSUES_FIXTURE)
    )
    connector = _make_connector()
    result = await connector.read_adapter("list_issues", project="my-app")
    assert len(result) == 1


@pytest.mark.unit
@respx.mock
async def test_write_adapter_dispatches_transition_issue():
    respx.post(f"{SONAR_BASE}/api/issues/do_transition").mock(
        return_value=httpx.Response(200, json={"issue": _ISSUE_ROW})
    )
    connector = _make_connector()
    result = await connector.write_adapter(
        "transition_issue", issue_key="AXabc123", target_state="confirm"
    )
    assert result["key"] == "AXabc123"


@pytest.mark.unit
async def test_read_adapter_rejects_unknown_operation():
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.read_adapter("not_a_real_operation")


@pytest.mark.unit
async def test_write_adapter_rejects_unknown_operation():
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.write_adapter("not_a_real_operation")


@pytest.mark.unit
@respx.mock
async def test_auth_uses_token_as_basic_username_with_empty_password(monkeypatch):
    """SonarQube's own convention: HTTP Basic with the token as username, no password."""
    route = respx.get(f"{SONAR_BASE}/api/projects/search").mock(
        return_value=httpx.Response(200, json=_PROJECT_FIXTURE)
    )
    import config.connectors.sonarqube as _sq_mod
    # __health_probe__ tenant skips the tenant secret store; env fallback supplies the token.
    monkeypatch.setattr(_sq_mod, "SONARQUBE_TOKEN", "my-token")
    connector = SonarQubeConnector(SONAR_BASE, tenant_id="__health_probe__")
    await connector.list_projects()
    sent_auth = route.calls.last.request.headers["Authorization"]
    import base64
    assert sent_auth == "Basic " + base64.b64encode(b"my-token:").decode()
