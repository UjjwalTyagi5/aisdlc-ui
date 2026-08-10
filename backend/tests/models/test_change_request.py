"""Tests for ChangeRequest model and its integration into SDLCWorkflowInput / RunCreateIn.

TDD: these tests are written BEFORE the implementation.
All tests must fail before implementation, then pass after.
"""
import pytest
from pydantic import ValidationError


def test_change_request_minimal():
    """ChangeRequest validates with only the required text field."""
    from shared.models.workflow_models import ChangeRequest
    cr = ChangeRequest(text="Add POST /orders")
    assert cr.text == "Add POST /orders"
    assert cr.kind is None
    assert cr.target_paths is None
    assert cr.work_item_id is None


def test_change_request_kind_feature():
    """ChangeRequest accepts 'feature' as a valid kind."""
    from shared.models.workflow_models import ChangeRequest
    cr = ChangeRequest(text="Add feature", kind="feature")
    assert cr.kind == "feature"


def test_change_request_all_valid_kinds():
    """ChangeRequest accepts all four Literal kind values."""
    from shared.models.workflow_models import ChangeRequest
    for kind in ("feature", "bugfix", "refactor", "test", "chore"):
        cr = ChangeRequest(text="x", kind=kind)
        assert cr.kind == kind


def test_change_request_invalid_kind_raises():
    """ChangeRequest raises ValidationError for an invalid kind."""
    from shared.models.workflow_models import ChangeRequest
    with pytest.raises(ValidationError):
        ChangeRequest(text="x", kind="nope")


def test_change_request_target_paths():
    """ChangeRequest stores target_paths list."""
    from shared.models.workflow_models import ChangeRequest
    cr = ChangeRequest(text="x", target_paths=["src/api.py", "src/models.py"])
    assert cr.target_paths == ["src/api.py", "src/models.py"]


def test_sdlc_workflow_input_with_change_request():
    """SDLCWorkflowInput validates with change_request populated."""
    from shared.models.workflow_models import ChangeRequest, SDLCWorkflowInput
    cr = ChangeRequest(text="x")
    inp = SDLCWorkflowInput(
        run_id="r",
        project_id="p",
        tenant_id="t",
        change_request=cr,
    )
    assert inp.change_request is not None
    assert inp.change_request.text == "x"


def test_sdlc_workflow_input_change_request_round_trips():
    """SDLCWorkflowInput with change_request round-trips through model_dump()."""
    from shared.models.workflow_models import ChangeRequest, SDLCWorkflowInput
    cr = ChangeRequest(text="Add POST /orders")
    inp = SDLCWorkflowInput(
        run_id="r",
        project_id="p",
        tenant_id="t",
        change_request=cr,
    )
    dumped = inp.model_dump()
    re_parsed = SDLCWorkflowInput(**dumped)
    assert re_parsed.change_request is not None
    assert re_parsed.change_request.text == "Add POST /orders"


def test_sdlc_workflow_input_without_change_request_defaults_none():
    """SDLCWorkflowInput without change_request defaults to None (backward-compatible)."""
    from shared.models.workflow_models import SDLCWorkflowInput
    inp = SDLCWorkflowInput(run_id="r", project_id="p", tenant_id="t")
    assert inp.change_request is None


def test_run_create_in_with_change_request():
    """RunCreateIn validates with change_request populated."""
    from shared.models.workflow_models import ChangeRequest
    from shared.routers._schemas import RunCreateIn
    cr = ChangeRequest(text="x")
    req = RunCreateIn(project_id="p", change_request=cr)
    assert req.change_request is not None
    assert req.change_request.text == "x"


def test_run_create_in_without_change_request_defaults_none():
    """RunCreateIn without change_request defaults to None (backward-compatible)."""
    from shared.routers._schemas import RunCreateIn
    req = RunCreateIn(project_id="p")
    assert req.change_request is None
