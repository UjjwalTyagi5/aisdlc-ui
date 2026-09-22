"""The Design agent is held to the project's tech stack where the stack is actually chosen:
in the generation tool's own model call, and on every chat turn.

LIVE (2026-09-22): skills reached the Design agent only as a list the chat model could choose
to read, and the Technology Stack section is written by `generate_architecture`'s own model
call, which saw none of it — so no stack rule ever held.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from agents_orchestrator.design_architecture_agent import components as dc  # noqa: E402
from agents_orchestrator.design_architecture_agent.agents import architecture as A  # noqa: E402
from shared.services import tech_stack_store  # noqa: E402
from shared.services.tech_stack import EffectiveTechStack, TechStack  # noqa: E402

NODE = TechStack(id="s1", scope="workspace", workspace_id="w1", project_id=None, name="Node + Next.js",
                 categories={"languages": ["TypeScript"], "backend_frameworks": ["Node.js", "Express"],
                             "frontend_frameworks": ["Next.js"], "databases": ["PostgreSQL"]})
EFF = EffectiveTechStack(NODE, "bu_default")
GOOD = ("## TECHNOLOGY STACK\n\n| Layer | Technology | Version | Justification |\n|---|---|---|---|\n"
        "| Frontend | Next.js | 14 | SSR |\n| Backend | Node.js (Express) | 20 | team |\n"
        "| Database | PostgreSQL | 16 | relational |\n")
BAD = GOOD.replace("| PostgreSQL | 16 |", "| MongoDB | 7 |")


def _llm(monkeypatch, *answers):
    prompts: list[str] = []
    replies = list(answers)

    async def fake(prompt, system=""):
        prompts.append(prompt)
        return replies.pop(0)

    monkeypatch.setattr(A, "_llm_generate_async", fake)
    monkeypatch.setattr(A, "broadcast_log", lambda *a, **k: None)   # no sockets in a test
    return prompts


async def test_the_generation_call_carries_the_stack_and_the_section_says_which(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    prompts = _llm(monkeypatch, GOOD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert "PROJECT TECH STACK — MANDATORY" in prompts[0] and "- Frontend frameworks: Next.js" in prompts[0]
    assert meta["model_calls"] == 1 and meta["tech_stack"] == "Node + Next.js" and meta["outside_stack"] == []
    assert "Project tech stack: **Node + Next.js** (the Business Unit default)." in out


async def test_a_table_outside_the_stack_is_corrected_once(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    prompts = _llm(monkeypatch, BAD, GOOD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert meta["model_calls"] == 2 and "MongoDB (Database)" in prompts[1] and "CORRECTION" in prompts[1]
    assert "MongoDB" not in out and meta["outside_stack"] == []


async def test_what_is_still_outside_after_the_correction_is_flagged_not_dropped(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    _llm(monkeypatch, BAD, BAD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert meta["model_calls"] == 2 and meta["outside_stack"] == ["MongoDB (Database)"]
    assert "> **Outside the project's tech stack:** MongoDB (Database)." in out
    assert "MongoDB (Database)" in A._tech_stack_receipt(meta)


async def test_without_a_stack_the_call_is_exactly_as_before(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=None))
    prompts = _llm(monkeypatch, BAD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    expected = (dc.build_generation_prompt(["stack"], custom_prompt="")
                + "\n--- DOCUMENT CONTENT (the source of requirements) ---\nBRD\n"
                + dc.existing_sections_note("", ["stack"]))
    assert prompts == [expected] and out == BAD and meta["model_calls"] == 1 and meta["tech_stack"] is None
    assert A._tech_stack_receipt(meta) == ""


async def test_other_sections_get_the_stack_but_no_table_check(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    prompts = _llm(monkeypatch, "## HIGH-LEVEL DESIGN\nuses MongoDB")
    out, meta = await A.generate_section_text("document content", "BRD", ["hld"], "", "")
    assert "PROJECT TECH STACK — MANDATORY" in prompts[0] and meta["model_calls"] == 1


async def test_a_warning_about_the_stack_reaches_the_receipt(monkeypatch):
    warned = EffectiveTechStack(NODE, "bu_default", "The tech stack chosen for this project was deleted.")
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=warned))
    _llm(monkeypatch, GOOD)
    _out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert "was deleted" in A._tech_stack_receipt(meta)
    assert "Tech stack applied: Node + Next.js (the Business Unit default)." in A._tech_stack_receipt(meta)


def test_the_chat_note_joins_the_system_message_without_a_second_one():
    msgs = [SystemMessage(content="You are the Design agent."), HumanMessage(content="hi")]
    out = A._with_tech_stack_note(msgs, "PROJECT TECH STACK — MANDATORY\n- Languages: Go")
    assert len(out) == 2 and isinstance(out[0], SystemMessage)
    assert out[0].content.endswith("- Languages: Go") and msgs[0].content == "You are the Design agent."
    assert A._with_tech_stack_note(msgs, "") is msgs
    bare = A._with_tech_stack_note([HumanMessage(content="hi")], "BLOCK")
    assert isinstance(bare[0], SystemMessage) and bare[0].content == "BLOCK"


def test_the_generation_prompt_places_the_block_before_the_templates():
    plain = dc.build_generation_prompt(["stack"])
    with_block = dc.build_generation_prompt(["stack"], tech_stack="PROJECT TECH STACK — MANDATORY")
    assert with_block != plain and "PROJECT TECH STACK — MANDATORY" in with_block
    # The grounding names the headers first; the block must come before the template itself.
    assert with_block.index("PROJECT TECH STACK") < with_block.index("| Layer")
