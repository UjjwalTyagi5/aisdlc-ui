from shared.services.orchestrator.progression import STAGE_ORDER, next_stage, gate_type_for


def test_stage_order_starts_requirements_then_design():
    assert STAGE_ORDER[0] == "requirements"
    assert STAGE_ORDER[1] == "design"


def test_next_stage_advances():
    assert next_stage("requirements") == "design"


def test_next_stage_end_returns_none():
    assert next_stage(STAGE_ORDER[-1]) is None


def test_gate_type_for_requirements_is_approval_required():
    assert gate_type_for("requirements") == "approval_required"


def test_gate_type_for_security_and_deployment_is_mandatory():
    # Blueprint §4.3: Security + Deployment gates are never auto-approvable by
    # policy (org_admin override still applies via _advance_decision's can_approve
    # branch — see copilot_api._advance_decision docstring).
    assert gate_type_for("security") == "mandatory"
    assert gate_type_for("deployment") == "mandatory"


def test_gate_type_for_requirements_and_design_stay_approval_required():
    assert gate_type_for("requirements") == "approval_required"
    assert gate_type_for("design") == "approval_required"


def test_gate_type_for_documentation_stays_auto_approve():
    assert gate_type_for("documentation") == "auto_approve"


def test_gate_type_for_testing_is_approval_required():
    # Testing must NOT auto-advance to Deployment — it gates so the user can run more
    # test types (functional/API) on the same branch before moving on.
    assert gate_type_for("testing") == "approval_required"
