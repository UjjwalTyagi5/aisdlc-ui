"""The architecture document must appear once, and its diagrams must parse.

TWO FAULTS FROM ONE LIVE SESSION.

1. THE DOCUMENT WAS SHOWN TWICE. generate_architecture_from_context streams the
   document to the client itself, token by token, as it generates
   (_llm_generate_async broadcasts stream_chunk). Its RETURN VALUE then arrives in
   the WS loop as a ToolMessage, and that loop streamed anything with content and no
   tool_calls -- so the same string went out again. Both copies truncated at the same
   word, which is what identified it as one string sent twice rather than the model
   repeating itself. The prompt already forbade repeating the tool output, and the
   model obeyed: its own reply was a single sentence.

2. TWO DIAGRAMS FAILED TO PARSE. Every mermaid block in the prompt's own templates
   was verified valid with mermaid 11.15 itself, so the model had deviated. The
   prompt now pins it to the four diagram types the templates use and names the
   pitfalls that break generated mermaid most often.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _prompt() -> str:
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        DESIGN_SYS_MESSAGE,
    )

    return " ".join(DESIGN_SYS_MESSAGE.split())


def _api_source() -> str:
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    return inspect.getsource(api)


# -- 1. the document is sent once ---------------------------------------------


@pytest.mark.unit
def test_tool_output_is_not_streamed_to_the_client():
    src = _api_source()
    assert "isinstance(msg_chunk, ToolMessage)" in src
    # It must SKIP the send, not skip the whole branch.
    idx = src.index("isinstance(msg_chunk, ToolMessage)")
    assert "continue" in src[idx: idx + 120]


@pytest.mark.unit
def test_tool_output_is_still_accumulated():
    """final_content feeds the transcript and _persist_design_artifacts, which parses
    the eight sections out of the DOCUMENT — and the document is the tool's output.
    Skipping the accumulation as well as the send would silently stop artifacts being
    saved, trading a visible bug for an invisible one."""
    src = _api_source()
    body = src[src.index("async for chunk in planning_app.astream"):]
    body = body[: body.index("except Exception")]
    # The accumulation must come BEFORE the ToolMessage skip.
    assert body.index("final_content += content") < body.index("isinstance(msg_chunk, ToolMessage)")


@pytest.mark.unit
def test_the_prompt_still_tells_the_model_not_to_repeat_the_document():
    """Belt and braces: the stream fix stops the duplicate, this stops a third copy."""
    p = _prompt()
    assert "Do NOT repeat or summarise the tool output" in p
    assert "already been streamed to the user" in p


# -- 2. diagrams that render ---------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("allowed", ["graph", "classDiagram", "sequenceDiagram", "erDiagram"])
def test_the_prompt_names_the_diagram_types_that_work(allowed):
    assert allowed in _prompt()


@pytest.mark.unit
@pytest.mark.parametrize(
    "banned", ["C4Context", "C4Container", "C4Component", "mindmap", "quadrantChart"]
)
def test_the_prompt_forbids_the_flaky_types(banned):
    """Mermaid's C4 support is experimental and fails often. The prompt's own C4
    sections are drawn with `graph TB`, so the model should follow those."""
    p = _prompt()
    assert banned in p
    assert "NEVER use C4Context" in p


@pytest.mark.unit
def test_the_prompt_requires_quoted_node_labels():
    """An unquoted label containing a bracket, parenthesis, comma, colon or slash is
    the most common cause of a diagram that will not parse."""
    p = _prompt()
    assert "Put EVERY node label in double quotes" in p


@pytest.mark.unit
def test_the_prompt_says_why_a_broken_diagram_matters():
    """Without the reason, "keep it simple" reads as a style note and gets ignored in
    favour of a more impressive diagram."""
    p = _prompt()
    assert "worse than a simpler diagram that works" in p


# -- the templates themselves stay valid --------------------------------------


@pytest.mark.unit
def test_every_template_diagram_declares_a_supported_type():
    """The eight mermaid blocks in the generation prompt were each checked against
    mermaid 11.15 and parse cleanly. This guards the cheaper property — that none of
    them uses a type the prompt now forbids — so a future edit cannot introduce one."""
    import re

    from agents_orchestrator.design_architecture_agent.prompts import (
        architecture_generation as ag,
    )

    src = Path(ag.__file__).read_text(encoding="utf-8")
    blocks = re.findall(r"```mermaid\r?\n(.*?)```", src, re.S)
    assert blocks, "no mermaid templates found"
    for block in blocks:
        kind = block.strip().split()[0]
        assert kind in {"graph", "flowchart", "classDiagram", "sequenceDiagram", "erDiagram"}, kind
