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


# -- standalone Design starts blank, on purpose --------------------------------
#
# A project-keyed fallback was added here and then REMOVED as a product decision.
# `build_context_for_project` would make the standalone Design page inherit whatever
# Requirements last produced — which is what Development does, and is not what this
# product wants for Design. Opening Project -> Design on its own is a blank-slate
# design conversation, not a silent continuation of a pipeline the user did not start.
#
# The formatter tests above still matter: they cover what Design receives through the
# ORCHESTRATOR, where the pipeline uses the run id as the session id.


@pytest.mark.unit
async def test_standalone_design_gets_no_context_when_the_session_has_none():
    """The whole point of the decision: no project fallback, so a fresh session id
    yields an empty context rather than the last run's payload."""
    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    by_project = AsyncMock(return_value="[REQUIREMENTS CONTEXT - Project: sdlc]")
    with patch.object(api, "build_context", AsyncMock(return_value="")),             patch("config.context_broker.build_context_for_project", by_project):
        ctx = await api._build_session_context("fresh-session")
    assert ctx == ""
    by_project.assert_not_awaited()


@pytest.mark.unit
async def test_the_pipeline_still_gets_its_run_context():
    """Inside the orchestrator the session id IS the run id, so the session-keyed
    lookup finds that run's artifacts. Removing the fallback must not break this."""
    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    with patch.object(api, "build_context", AsyncMock(return_value="from-session")):
        ctx = await api._build_session_context("run-id")
    assert ctx == "from-session"


@pytest.mark.unit
def test_design_does_not_reach_for_the_project_fallback():
    """A source-level guard, because the fallback is a one-line change somebody will
    reasonably re-add — Development uses it, and the docstring on
    build_context_for_project frames finding nothing as a bug."""
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    src = inspect.getsource(api)
    # A CALL or an IMPORT, not a mention — the docstring names the function on purpose,
    # to say why it is not used. A bare substring check would fail on the explanation.
    assert "build_context_for_project(" not in src.replace(
        "`build_context_for_project`", ""
    ), "standalone Design must start blank - see _build_session_context"
    assert "import build_context_for_project" not in src
