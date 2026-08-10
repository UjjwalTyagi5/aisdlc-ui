from shared.services.orchestrator import gate_routing as gr
from shared.services.orchestrator.gate_routing import gate_owner_role, can_user_approve

ALL_STAGES = [
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]


def test_requirements_owner_is_pm():
    assert gate_owner_role("requirements") == "product_manager"


def test_developer_cannot_approve_requirements():
    # developer perms lack artifact:approve_requirements
    assert can_user_approve(["run:create", "artifact:view", "connector:view"], "requirements") is False


def test_admin_wildcard_can_approve_any():
    assert can_user_approve(["admin:*"], "development") is True


def test_pm_can_approve_requirements():
    assert can_user_approve(["artifact:approve_requirements"], "requirements") is True


def test_admin_can_approve_every_stage():
    for s in ALL_STAGES:
        assert gr.can_user_approve(["admin:*"], s) is True, f"admin should approve {s}"


def test_every_stage_has_an_owner_role():
    for s in ALL_STAGES:
        assert gr.gate_owner_role(s)  # non-empty


def test_downstream_stages_have_a_permission_mapping():
    for s in ["code_review", "security", "deployment", "documentation"]:
        # the owning role's permission must approve its own stage
        owner_perm = gr.stage_approve_permission(s)  # add if missing (see Step 3)
        assert gr.can_user_approve([owner_perm], s) is True
