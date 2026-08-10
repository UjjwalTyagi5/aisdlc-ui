# platform/backend/tests/test_requirements_nlp_tools.py
import json

import pytest

from agents_orchestrator.requirements_agent.tools.nlp_quality_tool import run_nlp_quality_check


@pytest.mark.asyncio
async def test_nlp_quality_flags_weak_terms():
    out = await run_nlp_quality_check.ainvoke({"text": "The system should be fast and user-friendly."})
    data = json.loads(out)
    assert data["status"] == "ok"
    flagged = {w["term"] for w in data["weak_terms"]}
    assert "fast" in flagged
    assert "user-friendly" in flagged


@pytest.mark.asyncio
async def test_nlp_quality_clean_text_has_no_weak_terms():
    out = await run_nlp_quality_check.ainvoke({"text": "The API must respond within 200 milliseconds."})
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["weak_terms"] == []


@pytest.mark.asyncio
async def test_nlp_quality_empty_text():
    out = await run_nlp_quality_check.ainvoke({"text": "   "})
    data = json.loads(out)
    assert data["status"] == "empty"


from agents_orchestrator.requirements_agent.tools.smell_tool import run_requirement_smell_check


@pytest.mark.asyncio
async def test_smell_flags_untestable_verb():
    out = await run_requirement_smell_check.ainvoke({"text": "The system should support large files."})
    data = json.loads(out)
    assert data["status"] == "ok"
    smells = {s["smell"] for s in data["smells"]}
    assert "untestable_verb" in smells


@pytest.mark.asyncio
async def test_smell_flags_compound_requirement():
    out = await run_requirement_smell_check.ainvoke(
        {"text": "The service must validate input and store the record and email the user and log it."}
    )
    data = json.loads(out)
    smells = {s["smell"] for s in data["smells"]}
    assert "compound_requirement" in smells


@pytest.mark.asyncio
async def test_smell_clean_requirement():
    out = await run_requirement_smell_check.ainvoke(
        {"text": "The endpoint must return HTTP 200 within 200 milliseconds."}
    )
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["smells"] == []


from agents_orchestrator.requirements_agent.tools.spectral_tool import run_spectral_lint


@pytest.mark.asyncio
async def test_spectral_missing_path():
    out = await run_spectral_lint.ainvoke({"spec_path": "/no/such/spec.yaml"})
    data = json.loads(out)
    # Either CLI is absent (unavailable) or the path check fires (error) — never raise.
    assert data["status"] in ("unavailable", "error")
    assert data["findings"] == []
