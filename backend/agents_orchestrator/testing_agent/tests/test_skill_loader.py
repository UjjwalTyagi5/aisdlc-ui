"""Unit tests for the Skill loader (frontmatter parse, render, applies)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents_orchestrator.testing_agent.tools.skill_loader import (
    Skill,
    load_all_skills,
)


SKILLS_DIR = Path(__file__).parent.parent / "skills"


def test_load_unit_skill_from_disk():
    unit = Skill.load(SKILLS_DIR / "unit")
    assert unit.name == "unit"
    assert "pytest" in unit.prompt_template.lower() or "test" in unit.prompt_template.lower()
    assert "language" in unit.inputs


def test_render_substitutes_double_brace_variables():
    unit = Skill.load(SKILLS_DIR / "unit")
    rendered = unit.render(language="python", code_analysis="<analysis>", test_plan="<plan>")
    assert "{{language}}" not in rendered
    assert "python" in rendered


def test_unit_skill_applies_when_code_analysis_present():
    unit = Skill.load(SKILLS_DIR / "unit")
    state = {"code_analysis": object()}  # truthy
    assert unit.applies(state) is True


def test_unit_skill_does_not_apply_when_no_code_analysis():
    unit = Skill.load(SKILLS_DIR / "unit")
    state = {"code_analysis": None}
    assert unit.applies(state) is False


def test_load_all_skills_returns_dict_with_unit():
    skills = load_all_skills()
    assert "unit" in skills
    assert skills["unit"].name == "unit"


def test_missing_required_input_in_render_raises():
    unit = Skill.load(SKILLS_DIR / "unit")
    # `language` is declared as required input
    with pytest.raises(KeyError):
        unit.render(code_analysis="<x>", test_plan="<y>")  # missing language


def test_negative_edge_applies_when_code_analysis_present():
    skills = load_all_skills(reload=True)
    ne = skills.get("negative_edge")
    assert ne is not None
    assert ne.applies({"code_analysis": object()}) is True
    assert ne.applies({}) is False


def test_smoke_applies_when_url_set():
    skills = load_all_skills(reload=True)
    smoke = skills["smoke"]
    assert smoke.applies({"target_url": "http://localhost:8000"}) is True
    assert smoke.applies({"target_url": None}) is False
    assert smoke.applies({
        "target_url": None,
        "upstream_development": {"service_url": "http://x"},
    }) is True


def test_functional_api_applies():
    skills = load_all_skills(reload=True)
    fa = skills["functional_api"]
    assert fa.applies({"target_url": "http://x"}) is True
    assert fa.applies({"upstream_development": {"api_routes": [{"path": "/x"}]}}) is True
    assert fa.applies({}) is False
