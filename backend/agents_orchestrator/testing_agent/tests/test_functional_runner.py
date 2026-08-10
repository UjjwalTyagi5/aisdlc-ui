"""FunctionalRunner: route discovery, scenario execution, JUnit XML output."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import respx
from httpx import Response

from agents_orchestrator.testing_agent.tools.functional_runner import (
    FunctionalRunner,
    Route,
    Scenario,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_discover_routes_from_openapi():
    runner = FunctionalRunner()
    spec = json.loads((FIXTURE_DIR / "openapi_fixture.json").read_text())
    routes = runner.routes_from_openapi(spec)
    paths = {r.path for r in routes}
    assert "/health" in paths
    assert "/users/{id}" in paths


@pytest.mark.asyncio
@respx.mock
async def test_execute_scenarios_records_status_and_passed():
    respx.get("http://api.test/health").mock(return_value=Response(200, json={"status": "ok"}))
    respx.get("http://api.test/users/1").mock(return_value=Response(404, json={}))

    runner = FunctionalRunner()
    scenarios = [
        Scenario(scenario_id="FS-001", method="GET", path="/health", expected_status=200),
        Scenario(scenario_id="FS-002", method="GET", path="/users/1", expected_status=200),
    ]
    results = await runner.execute_scenarios("http://api.test", scenarios)
    assert results[0].passed is True
    assert results[0].status_code_actual == 200
    assert results[1].passed is False
    assert results[1].status_code_actual == 404


def test_write_results_xml(tmp_path):
    from shared.models import FunctionalScenarioResult

    runner = FunctionalRunner()
    results = [
        FunctionalScenarioResult(
            scenario_id="FS-001", method="GET", path="/health",
            status_code_expected=200, status_code_actual=200, passed=True,
        ),
        FunctionalScenarioResult(
            scenario_id="FS-002", method="GET", path="/users/1",
            status_code_expected=200, status_code_actual=404, passed=False,
            error="expected 200, got 404",
        ),
    ]
    out_path = tmp_path / "functional_results.xml"
    runner.write_results_xml(results, str(out_path))
    tree = ET.parse(str(out_path))
    root = tree.getroot()
    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "1"
