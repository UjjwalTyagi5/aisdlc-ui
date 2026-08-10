"""FunctionalRunner — httpx-driven black-box test executor.

Used by the functional_api skill's generated test file (which runs under
PythonRunner via pytest) AND directly by the testing-agent for ad-hoc
URL-driven scenarios that don't fit a pytest harness.

Output: JUnit-shaped XML at reports/functional_results.xml so the existing
_parse_junit_xml helper ingests it.
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx

from shared.models import FunctionalScenarioResult

logger = logging.getLogger("testing_agent.functional_runner")


@dataclass
class Route:
    method: str
    path: str
    response_codes: List[int]


@dataclass
class Scenario:
    scenario_id: str
    method: str
    path: str
    expected_status: int
    body: Dict[str, Any] | None = None
    headers: Dict[str, str] | None = None


class FunctionalRunner:

    def routes_from_openapi(self, spec: Dict[str, Any]) -> List[Route]:
        out: List[Route] = []
        for path, methods in (spec.get("paths") or {}).items():
            for method, op in methods.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete"):
                    continue
                codes = [int(c) for c in (op.get("responses") or {}).keys() if str(c).isdigit()]
                out.append(Route(method=method.upper(), path=path, response_codes=codes))
        return out

    async def execute_scenarios(
        self,
        base_url: str,
        scenarios: List[Scenario],
        timeout_s: float = 10.0,
    ) -> List[FunctionalScenarioResult]:
        results: List[FunctionalScenarioResult] = []
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
            for sc in scenarios:
                actual = None
                error = None
                sample = None
                try:
                    resp = await client.request(
                        sc.method, sc.path, json=sc.body, headers=sc.headers,
                    )
                    actual = resp.status_code
                    body = resp.text or ""
                    sample = body[:500]
                    passed = actual == sc.expected_status
                except Exception as exc:
                    passed = False
                    error = f"{type(exc).__name__}: {exc}"
                results.append(FunctionalScenarioResult(
                    scenario_id=sc.scenario_id,
                    method=sc.method,
                    path=sc.path,
                    status_code_expected=sc.expected_status,
                    status_code_actual=actual,
                    passed=passed,
                    response_sample=sample,
                    error=error,
                ))
        return results

    def write_results_xml(self, results: List[FunctionalScenarioResult], path: str) -> None:
        total = len(results)
        failures = sum(1 for r in results if not r.passed)
        suite = ET.Element("testsuite", attrib={
            "name": "functional",
            "tests": str(total),
            "failures": str(failures),
            "errors": "0",
            "skipped": "0",
        })
        for r in results:
            tc = ET.SubElement(suite, "testcase", attrib={
                "classname": "FunctionalAPI",
                "name": f"{r.method}_{r.path.replace('/', '_').strip('_')}",
                "time": "0.0",
            })
            if not r.passed:
                fail = ET.SubElement(tc, "failure", attrib={
                    "message": r.error or f"expected {r.status_code_expected}, got {r.status_code_actual}",
                })
                fail.text = r.response_sample or ""
        tree = ET.ElementTree(suite)
        tree.write(path, encoding="utf-8", xml_declaration=True)
