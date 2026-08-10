"""Phase 11.3 — applies() rules for the 4 new LLM-only skills.

Each rule is unit-tested in isolation; the SKILL.md files themselves are
exercised via the existing skill_loader test that loads everything from disk.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents_orchestrator.testing_agent.tools.skill_loader import (
    Skill,
    load_all_skills,
)


# --- Disk loadability — all 9 skills load without error ---------------------

def test_all_skills_load_from_disk():
    """All Phase 11 skills must parse cleanly: 5 original + 4 LLM (Phase 11.3)
    + 3 shell (Phase 11.4) = 12 total."""
    skills = load_all_skills(reload=True)
    # Phase 11.3 — LLM-only additions
    assert "integration" in skills
    assert "contract" in skills
    assert "accessibility" in skills
    assert "property_based" in skills
    # Existing 5 still loadable
    assert "unit" in skills
    assert "negative_edge" in skills
    assert "smoke" in skills
    assert "functional_api" in skills
    assert "functional_ui" in skills
    # Phase 11.4 — shell-runtime additions
    assert "mutation_testing" in skills
    assert "security_static" in skills
    assert "dependency_scan" in skills
    assert len(skills) == 12


# --- integration -----------------------------------------------------------

def test_integration_applies_when_two_or_more_file_paths():
    skills = load_all_skills(reload=True)
    integ = skills["integration"]

    fns_two = [
        MagicMock(file_path="services/auth.py", function_name="login"),
        MagicMock(file_path="controllers/cases.py", function_name="answer"),
    ]
    state = {"code_analysis": MagicMock(functions=fns_two)}
    assert integ.applies(state) is True


def test_integration_skips_when_only_one_file_path():
    skills = load_all_skills(reload=True)
    integ = skills["integration"]
    fns_one = [
        MagicMock(file_path="services/auth.py", function_name="login"),
        MagicMock(file_path="services/auth.py", function_name="logout"),
    ]
    state = {"code_analysis": MagicMock(functions=fns_one)}
    assert integ.applies(state) is False


def test_integration_skips_with_no_code_analysis():
    skills = load_all_skills(reload=True)
    assert skills["integration"].applies({}) is False


# --- contract --------------------------------------------------------------

def test_contract_applies_when_api_contracts_dict_non_empty():
    skills = load_all_skills(reload=True)
    state = {"upstream_design": {"api_contracts": {"GET /users": {"response": "User[]"}}}}
    assert skills["contract"].applies(state) is True


def test_contract_applies_when_api_contracts_string_non_empty():
    skills = load_all_skills(reload=True)
    state = {"upstream_design": {"api_contracts": "## API Contracts\n- GET /users -> User[]"}}
    assert skills["contract"].applies(state) is True


def test_contract_skips_when_no_design():
    skills = load_all_skills(reload=True)
    assert skills["contract"].applies({}) is False
    assert skills["contract"].applies({"upstream_design": None}) is False


def test_contract_skips_when_api_contracts_empty():
    skills = load_all_skills(reload=True)
    assert skills["contract"].applies({"upstream_design": {"api_contracts": {}}}) is False
    assert skills["contract"].applies({"upstream_design": {"api_contracts": ""}}) is False


# --- accessibility ---------------------------------------------------------

def test_accessibility_applies_for_react_with_jsx_files():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(file_path="src/Button.jsx", function_name="Button")]
    state = {"language": "react", "code_analysis": MagicMock(functions=fns)}
    assert skills["accessibility"].applies(state) is True


def test_accessibility_applies_for_react_with_tsx_files():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(file_path="src/Button.tsx", function_name="Button")]
    state = {"language": "react", "code_analysis": MagicMock(functions=fns)}
    assert skills["accessibility"].applies(state) is True


def test_accessibility_skips_for_python():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(file_path="src/main.py", function_name="run")]
    state = {"language": "python", "code_analysis": MagicMock(functions=fns)}
    assert skills["accessibility"].applies(state) is False


def test_accessibility_skips_for_dotnet():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(file_path="Services/Foo.cs", function_name="Run")]
    state = {"language": "dotnet", "code_analysis": MagicMock(functions=fns)}
    assert skills["accessibility"].applies(state) is False


def test_accessibility_skips_react_without_components():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(file_path="src/util.js", function_name="helper")]
    state = {"language": "react", "code_analysis": MagicMock(functions=fns)}
    assert skills["accessibility"].applies(state) is False


# --- property_based --------------------------------------------------------

def test_property_based_applies_when_typed_input_present():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(input_format="x: int, y: int", function_name="add")]
    state = {"code_analysis": MagicMock(functions=fns)}
    assert skills["property_based"].applies(state) is True


def test_property_based_applies_with_collection_types():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(input_format="items: list[str]", function_name="sort_strings")]
    state = {"code_analysis": MagicMock(functions=fns)}
    assert skills["property_based"].applies(state) is True


def test_property_based_skips_when_only_object_inputs():
    skills = load_all_skills(reload=True)
    fns = [MagicMock(input_format="user: UserDTO with name and email", function_name="save")]
    state = {"code_analysis": MagicMock(functions=fns)}
    assert skills["property_based"].applies(state) is False


def test_property_based_skips_with_no_code_analysis():
    skills = load_all_skills(reload=True)
    assert skills["property_based"].applies({}) is False


# --- combined applies — full state matrix ----------------------------------

def test_full_applies_matrix_for_dotnet_codeonly():
    """Same case as the user's screenshot run: dotnet repo, no service URL,
    code analysis present with multiple files. After Phase 11.3 we expect
    integration + property_based to ALSO apply alongside unit + negative_edge."""
    skills = load_all_skills(reload=True)
    fns = [
        MagicMock(file_path="Services/A.cs", function_name="DoA",
                  input_format="x: int"),
        MagicMock(file_path="Services/B.cs", function_name="DoB",
                  input_format="ids: list[int]"),
        MagicMock(file_path="Controllers/Cases.cs", function_name="AnswerDup",
                  input_format="vm: AnswerDupQuestionViewModel"),
    ]
    state = {
        "language": "dotnet",
        "code_analysis": MagicMock(functions=fns),
    }
    applicable = sorted(name for name, s in skills.items() if s.applies(state))
    # Pre-Phase 11.3 was just [unit, negative_edge]. Now expect 4.
    assert "unit" in applicable
    assert "negative_edge" in applicable
    assert "integration" in applicable
    assert "property_based" in applicable
    # Without URL / design, smoke / functional_* / contract still skip
    assert "smoke" not in applicable
    assert "functional_api" not in applicable
    assert "functional_ui" not in applicable
    assert "contract" not in applicable
    # Accessibility skips because language is dotnet
    assert "accessibility" not in applicable


def test_full_applies_matrix_for_react_with_design():
    """React project + design contracts → all 4 new skills apply (plus existing)."""
    skills = load_all_skills(reload=True)
    fns = [
        MagicMock(file_path="src/Button.jsx", function_name="Button",
                  input_format="props: { label: string }"),
        MagicMock(file_path="src/Card.jsx", function_name="Card",
                  input_format="props: { title: string }"),
    ]
    state = {
        "language": "react",
        "target_url": "http://localhost:3000",
        "code_analysis": MagicMock(functions=fns),
        "upstream_design": {"api_contracts": {"GET /users": {"response": "User[]"}}},
    }
    applicable = sorted(name for name, s in skills.items() if s.applies(state))
    # All 9 skills apply except functional_api (no api_routes/openapi)
    expected = {"unit", "negative_edge", "integration", "property_based",
                "accessibility", "contract", "smoke", "functional_api", "functional_ui"}
    assert set(applicable) == expected
