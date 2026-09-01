"""What Design actually receives from Requirements, and whether it receives it at all.

TWO FAULTS, both silent.

1. ON THE STANDALONE PAGE, DESIGN GOT NOTHING. `build_context` is keyed by session id.
   That works in the orchestrator, where the pipeline uses the run id AS the session id
   — but opening Project -> Design directly mints a fresh session id unrelated to
   whatever session Requirements used, so the lookup found nothing on a project whose
   Requirements had been fully baselined. `build_context_for_project` exists precisely
   for this and the Development agent already used it; Design did not.

2. THE FORMATTER DROPPED MOST OF THE PAYLOAD. `build_requirements_payload` produces 11
   fields; `_fmt_requirements` rendered 6. Design's registry declares exactly one input,
   so anything this drops, Design never learns.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAYLOAD = {
    "project": "sdlc",
    "provider_kind": "azure_devops",
    "scope_summary": "An agentic SDLC platform for regulated delivery.",
    "stories": [{"title": "Ingest a BRD", "acceptance_criteria": ["Given a BRD", "Then stories"]}],
    "work_items": [
        {"id": 1, "source_key": "1", "provider_kind": "azure_devops",
         "type": "Epic", "title": "Project Initiation"},
    ],
    "assumptions": ["Single tenant for pilot"],
    "out_of_scope": ["Mobile app", "Offline mode"],
    "non_functional_requirements": {"latency": "p95 < 300ms"},
}


def _fmt(payload=None):
    from config.context_broker import _fmt_requirements

    return _fmt_requirements(payload if payload is not None else PAYLOAD)


# ── the fields that used to be dropped ───────────────────────────────────────


@pytest.mark.unit
def test_board_work_items_reach_design():
    """THE TRACEABILITY LINK. Design's registry lists `traceability.map` as REQUIRED and
    DesignArtifacts has `linked_work_item_ids` — neither had any source before this."""
    out = _fmt()
    assert "Board Work Items" in out
    assert "#1" in out and "Project Initiation" in out


@pytest.mark.unit
def test_out_of_scope_reaches_design():
    """The one that is a correctness risk rather than a gap: without it Design can
    architect something Requirements explicitly excluded."""
    out = _fmt()
    assert "OUT OF SCOPE" in out
    assert "Mobile app" in out and "Offline mode" in out


@pytest.mark.unit
def test_scope_summary_and_assumptions_reach_design():
    out = _fmt()
    assert "An agentic SDLC platform" in out
    assert "Single tenant for pilot" in out


@pytest.mark.unit
def test_nfrs_render_their_values_not_just_their_keys():
    """The payload builds this as a DICT; the formatter iterated it as a list, which
    yields keys only — so every NFR value was discarded once populated."""
    out = _fmt()
    assert "latency" in out and "p95 < 300ms" in out


@pytest.mark.unit
def test_acceptance_criteria_lists_are_not_rendered_as_python_reprs():
    out = _fmt()
    assert "['Given a BRD'" not in out
    assert "Given a BRD; Then stories" in out


# ── truncation is announced ──────────────────────────────────────────────────


@pytest.mark.unit
def test_a_long_brd_says_it_was_truncated():
    """It was silently cut to 600 characters — roughly the first paragraph of a
    document running to thousands of words, with nothing to indicate the rest."""
    out = _fmt({**PAYLOAD, "brd_content": "x" * 9000})
    assert "TRUNCATED" in out
    assert "9000 characters" in out


@pytest.mark.unit
def test_a_short_brd_is_not_labelled_truncated():
    out = _fmt({**PAYLOAD, "brd_content": "A short brief."})
    assert "TRUNCATED" not in out
    assert "A short brief." in out


@pytest.mark.unit
def test_story_truncation_is_announced():
    many = [{"title": f"Story {i}"} for i in range(47)]
    out = _fmt({**PAYLOAD, "stories": many})
    assert "47" in out and "showing first 20" in out


@pytest.mark.unit
def test_an_empty_payload_does_not_crash_or_invent_sections():
    out = _fmt({})
    assert "REQUIREMENTS CONTEXT" in out
    for absent in ("OUT OF SCOPE", "Board Work Items", "Assumptions", "TRUNCATED"):
        assert absent not in out


# ── Design finds the context on the standalone page ──────────────────────────


@pytest.mark.unit
async def test_design_falls_back_to_the_project_when_the_session_has_nothing():
    """The standalone-page case: a fresh session id resolves to nothing, so the
    project's most recent Run has to answer instead."""
    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    by_project = AsyncMock(return_value="[REQUIREMENTS CONTEXT — Project: sdlc]")
    with patch.object(api, "build_context", AsyncMock(return_value="")), \
            patch("config.context_broker.build_context_for_project", by_project):
        ctx = await api._build_session_context("fresh-session", "proj-1", "tenant-1")
    assert "REQUIREMENTS CONTEXT" in ctx
    assert by_project.await_args.args == ("proj-1", "tenant-1", "design")


@pytest.mark.unit
async def test_the_session_answer_wins_when_there_is_one():
    """Inside a pipeline run the session names THIS run's artifacts; the project read
    would return the latest run's, which may be a different one."""
    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    by_project = AsyncMock(return_value="from-project")
    with patch.object(api, "build_context", AsyncMock(return_value="from-session")), \
            patch("config.context_broker.build_context_for_project", by_project):
        ctx = await api._build_session_context("run-id", "proj-1", "tenant-1")
    assert ctx == "from-session"
    by_project.assert_not_awaited()


@pytest.mark.unit
async def test_no_project_context_means_no_fallback_rather_than_a_bad_lookup():
    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    by_project = AsyncMock(return_value="should-not-be-used")
    with patch.object(api, "build_context", AsyncMock(return_value="")), \
            patch("config.context_broker.build_context_for_project", by_project):
        ctx = await api._build_session_context("fresh-session", "", "")
    assert ctx == ""
    by_project.assert_not_awaited()
