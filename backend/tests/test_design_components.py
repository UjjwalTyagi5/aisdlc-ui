"""The Design agent's component catalogue — the one list three readers derive from.

WHY IT EXISTS. Asked for "the HLD", the Design agent produced the whole eight-section
document plus an executive summary, deployment plan, risks and future work, because its
prompt mandated "ALL 8 sections, EVERY response" and its template was one 435-line
block. The catalogue is what lets a request name components, the generation prompt
contain only their templates, and the agent tell the user what it can produce — from
data, so the three cannot drift.

What is pinned:

  * the ids, their document order, and that every parsed section header
    (`shared/models/design.py::_SECTION_MAP`) is a catalogue header — the parser and the
    catalogue must agree, or a produced section is invisible on the page;
  * `build_generation_prompt` contains the selected components' templates and headers
    and NOT the others', and its diagram checklist names only their diagrams;
  * unknown ids are refused by name, and an empty selection is refused;
  * `overview` rides along only with the full document;
  * the menu text names every component with what it yields.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.design_architecture_agent import components as dc


def test_the_catalogue_is_in_document_order_and_complete():
    ids = [c.id for c in dc.COMPONENTS]
    assert ids == ["overview", "hld", "lld", "c4", "api", "db", "adr", "stack", "security"]
    assert set(dc.COMPONENT_IDS) == set(ids)


def test_every_parsed_section_header_is_a_catalogue_header():
    """`parse_artifact_sections` extracts by exact header; the templates must emit
    exactly those, or the page shows an empty tab for a section that was written."""
    from shared.models.design import _SECTION_MAP

    headers = {c.header for c in dc.COMPONENTS}
    for header in _SECTION_MAP.values():
        assert header in headers, header


def test_headers_are_the_uppercase_form_the_prompt_mandates():
    for c in dc.COMPONENTS:
        assert c.header == c.header.upper(), c.header
        assert c.template.lstrip().startswith(f"## {c.header}"), (
            f"{c.id}'s template must open with its exact header"
        )


def test_the_menu_names_every_component_and_what_it_yields():
    menu = dc.menu_text()
    for c in dc.COMPONENTS:
        if c.id == "overview":
            continue  # part of the full document, not something to pick alone
        assert c.label in menu, c.label
        assert c.yields in menu, c.yields
    assert "full design document" in menu.lower()


@pytest.mark.parametrize("header, expected", [
    ("HIGH-LEVEL DESIGN", "hld"),
    ("High-Level Design (HLD)", "hld"),
    ("High-Level Design (HLD) [REQUIRED]", "hld"),
    ("Low-Level Design (LLD)", "lld"),
    ("C4 Architecture Diagram", "c4"),
    ("API Contracts", "api"),
    ("Database Schema", "db"),
    ("Architecture Decision Records (ADRs)", "adr"),
    ("Technology Stack & Infrastructure", "stack"),
    ("Security Architecture", "security"),
    ("Deployment Architecture", "stack"),  # deployment is part of the stack component
    ("Future Enhancements", None),
    ("", None),
])
def test_a_header_is_recognised_in_its_older_decorated_forms_too(header, expected):
    """Documents already in the record were written by the retired single-block
    template, whose headers carried "(HLD)" and "[REQUIRED]". They must still merge
    and label as the component they are."""
    assert dc.component_for_header(header) == expected


def test_resolve_accepts_ids_labels_and_aliases():
    assert dc.resolve(["hld"]) == ["hld"]
    assert dc.resolve(["High-level design", "database schema"]) == ["hld", "db"]
    assert dc.resolve(["HLD", "DB", "ERD"]) == ["hld", "db"]
    assert dc.resolve(["api contract", "openapi"]) == ["api"]


def test_resolve_keeps_document_order_not_request_order():
    assert dc.resolve(["security", "hld", "db"]) == ["hld", "db", "security"]


def test_resolve_refuses_an_unknown_component_by_name():
    with pytest.raises(dc.UnknownComponentError) as info:
        dc.resolve(["hld", "deployment runbook"])
    assert "deployment runbook" in str(info.value)
    assert "High-level design" in str(info.value), "the refusal must list what exists"


def test_resolve_refuses_an_empty_selection():
    with pytest.raises(dc.UnknownComponentError):
        dc.resolve([])


def test_the_full_document_is_every_component_including_the_overview():
    assert dc.resolve(["all"]) == [c.id for c in dc.COMPONENTS]
    assert dc.resolve(["full design document"]) == [c.id for c in dc.COMPONENTS]
    assert "overview" not in dc.resolve(["hld", "db"])


# ── the generation prompt ─────────────────────────────────────────────────────


def test_the_prompt_contains_only_the_selected_templates():
    prompt = dc.build_generation_prompt(["hld", "db"], custom_prompt="focus on latency")

    assert "## HIGH-LEVEL DESIGN" in prompt
    assert "## DATABASE SCHEMA" in prompt
    for absent in ("## LOW-LEVEL DESIGN", "## API CONTRACT", "## SECURITY DESIGN CHECKLIST",
                   "## TECHNOLOGY STACK", "## ARCHITECTURE DECISION RECORDS", "## C4 ARCHITECTURE DIAGRAMS"):
        assert absent not in prompt, absent
    assert "focus on latency" in prompt


def test_the_prompt_says_to_produce_nothing_else():
    prompt = dc.build_generation_prompt(["hld"])
    lowered = prompt.lower()
    assert "only" in lowered and "do not add" in lowered


def test_the_diagram_checklist_covers_only_the_selected_components():
    prompt = dc.build_generation_prompt(["db"])
    assert "erDiagram" in prompt
    assert "sequenceDiagram" not in prompt, "the LLD's diagram must not be demanded for a DB schema"
    assert "C4 level" not in prompt, "the C4 diagrams must not be demanded for a DB schema"


def test_the_prompt_keeps_the_grounding_rules_whatever_is_selected():
    for ids in (["hld"], ["security"], ["all"]):
        prompt = dc.build_generation_prompt(ids)
        assert "[ASSUMPTION]" in prompt
        assert "GROUNDING" in prompt


def test_the_full_prompt_carries_every_header_in_order():
    prompt = dc.build_generation_prompt(["all"])
    positions = [prompt.index(f"## {c.header}") for c in dc.COMPONENTS]
    assert positions == sorted(positions)
