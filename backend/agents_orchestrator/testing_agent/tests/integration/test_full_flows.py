"""Integration: full fan-out → aggregator → QA report against fixture FastAPI.

Asserts on the structure of the produced QA report (HTML + paths in artifact),
NOT on the LLM-generated code's correctness (LLM is mocked / deterministic).
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import uvicorn


@pytest.fixture(scope="module")
def fixture_server():
    """Boot fixture FastAPI on a random localhost port for the test module."""
    from agents_orchestrator.testing_agent.tests.integration.fixture_app import app
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    import time
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn did not start within 5s")
    sock = server.servers[0].sockets[0]
    port = sock.getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.mark.asyncio
async def test_dispatch_to_qa_report_with_running_service(fixture_server, tmp_path):
    """End-to-end: state with target_url → fan-out runs → aggregator collects → QA report renders."""
    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import dispatch_test_types
    from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import aggregate_test_results
    from agents_orchestrator.testing_agent.Nodes.generate_qa_report import generate_qa_report

    state = {
        "session_id": "INT-001",
        "language": "python",
        "work_dir": str(tmp_path),
        "output_dir": str(tmp_path / "output"),
        "target_url": fixture_server,
        "code_analysis": MagicMock(functions=[MagicMock()], model_dump_json=lambda: "{}"),
        "test_plan": MagicMock(test_cases=[], model_dump_json=lambda: "{}"),
    }

    # Stub LLM so we don't hit the real API. Each skill returns a tiny pytest stub.
    fake_chain = MagicMock()
    fake_chain.invoke = lambda prompt: "import pytest\ndef test_smoke(): assert True\n"
    fake_llm = MagicMock()
    fake_llm.__or__ = lambda self, other: fake_chain

    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types.get_llm",
        return_value=fake_llm,
    ):
        delta1 = await dispatch_test_types(state)
    state.update(delta1)

    delta2 = await aggregate_test_results(state)
    state.update(delta2)

    delta3 = await generate_qa_report(state)
    state.update(delta3)

    assert state["qa_report_html_path"] is not None
    html = Path(state["qa_report_html_path"]).read_text()
    assert "Test Strategy" in html
    assert "unit" in html.lower() or "Unit" in html
