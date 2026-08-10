"""BYOK P5 A1 — per-run model_id threading tests (live Postgres).

Covers the exact resolver branch POST /runs relies on for validation
(resolve_model_for_run accept/reject) plus the pydantic plumbing that
carries model_id from the request body through to the workflow input.
"""
import uuid

import pytest


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _seed_valid_provider(tenant, models, default_model):
    """Create a provider, force status='valid', set one offering as default.

    Mirrors the helper in tests/test_model_resolver.py.
    """
    from shared.services import model_config as mc
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme",
        api_key="sk-byok-xyz", enabled_models=models, created_by="admin1",
    )
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE id=:i AND tenant_id=:t"),
                        {"i": created["id"], "t": tenant})
        await s.execute(
            text("UPDATE model_offerings SET is_default=true WHERE provider_id=:p AND model_id=:m AND tenant_id=:t"),
            {"p": created["id"], "m": default_model, "t": tenant})
    return created


# ── Validation branch that POST /runs invokes before creating the Run row ──────

@pytest.mark.asyncio
async def test_post_runs_validation_accepts_enabled_model():
    """The success branch: an enabled model_id resolves without raising."""
    from shared.services.model_resolver import resolve_model_for_run
    tenant = str(uuid.uuid4())
    await _seed_valid_provider(tenant, ["claude-sonnet-4-6", "claude-opus-4-8"], "claude-opus-4-8")

    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6")

    assert resolved.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_post_runs_validation_rejects_not_enabled_model():
    """The 422 branch: a model not enabled for the org raises ModelNotEnabledError."""
    from shared.services.model_resolver import resolve_model_for_run, ModelNotEnabledError
    tenant = str(uuid.uuid4())
    await _seed_valid_provider(tenant, ["claude-sonnet-4-6"], "claude-sonnet-4-6")

    with pytest.raises(ModelNotEnabledError):
        await resolve_model_for_run(tenant, "claude-opus-4-8")


@pytest.mark.asyncio
async def test_post_runs_validation_fails_closed_no_provider():
    """The 409 branch: no configured provider raises NoModelConfiguredError."""
    from shared.services.model_resolver import resolve_model_for_run, NoModelConfiguredError
    tenant = str(uuid.uuid4())  # nothing configured
    with pytest.raises(NoModelConfiguredError):
        await resolve_model_for_run(tenant, "claude-sonnet-4-6")


# ── Pydantic plumbing: model_id present + defaults to None ─────────────────────

def test_run_create_in_accepts_model_id():
    from shared.routers._schemas import RunCreateIn
    body = RunCreateIn(project_id="p1", model_id="claude-sonnet-4-6")
    assert body.model_id == "claude-sonnet-4-6"


def test_run_create_in_model_id_defaults_none():
    from shared.routers._schemas import RunCreateIn
    body = RunCreateIn(project_id="p1")
    assert body.model_id is None


def test_workflow_input_accepts_model_id():
    from shared.models.workflow_models import SDLCWorkflowInput
    wf = SDLCWorkflowInput(
        run_id="r1", project_id="p1", tenant_id="t1", model_id="claude-opus-4-8",
    )
    assert wf.model_id == "claude-opus-4-8"


def test_workflow_input_model_id_defaults_none():
    from shared.models.workflow_models import SDLCWorkflowInput
    wf = SDLCWorkflowInput(run_id="r1", project_id="p1", tenant_id="t1")
    assert wf.model_id is None
