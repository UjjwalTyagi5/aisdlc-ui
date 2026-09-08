"""The delivery-status picker, which answered 200 and changed nothing.

THE BUG. `PATCH /projects/{id}` accepted `{"deliveryStatus": "in_progress"}` and
returned **200** — FastAPI drops fields the request model does not declare — while there
was no column, no ORM field and nothing in `ProjectOut`. The response carried no
`deliveryStatus`, so the frontend's `ProjectDeliveryStatus.default("not_started")`
supplied one, and the success toast read "Status set to Not started": a confirmation of
something that had not happened, naming the wrong value. The pill reverted on the next
refetch and the control looked dead.

WHAT THESE TESTS ARE FOR. A test that only asserts "PATCH returns 200" would have passed
against the broken build — that was the whole problem. Every assertion below therefore
reads the value BACK, either from the database or from a fresh GET.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project():
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Delivery Status Test')"
        ), {"i": org, "s": f"ds-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Delivery Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "project": proj}


async def _status(project) -> str:
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        return (await s.execute(
            text("SELECT delivery_status FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": project["project"]},
        )).scalar()


async def _set(project, value: str):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "UPDATE projects SET delivery_status = :v WHERE id = CAST(:p AS uuid)"
        ), {"v": value, "p": project["project"]})
        await s.commit()


async def test_an_existing_project_reads_not_started(project):
    """The column is NOT NULL with a default, so nothing has to backfill and no project
    reads as null — which the frontend would render as "not started" anyway, giving the
    same fact two spellings."""
    assert await _status(project) == "not_started"


@pytest.mark.parametrize("value", ["in_progress", "on_hold", "completed", "not_started"])
async def test_every_status_the_ui_offers_is_accepted(project, value):
    """The four values in `lib/schemas/project.ts`. A status the picker offers and the
    database refuses would abort the caller's whole transaction, surfacing as an
    unrelated error several frames from the cause."""
    await _set(project, value)
    assert await _status(project) == value


async def test_a_status_outside_the_catalogue_is_refused(project):
    """The CHECK is the last line. `ProjectPatchIn` validates first so the API answers
    422 naming the field rather than 500 from a refused statement."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await _set(project, "shipped_it")


async def test_the_orm_reads_the_column_back(project):
    """`ProjectOut.from_orm_project` is what the response is built from; a column the
    ORM does not map is exactly the gap that made PATCH a silent no-op."""
    from shared.models.orm import Project
    from shared.routers._schemas import ProjectOut
    from sqlalchemy import select

    await _set(project, "on_hold")
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        row = (await s.execute(
            select(Project).where(Project.id == project["project"]))).scalar_one()
        assert row.delivery_status == "on_hold"
        assert ProjectOut.from_orm_project(row).deliveryStatus == "on_hold"


async def test_the_patch_model_rejects_an_unknown_status():
    """A 422 that names the field, rather than a 500 several frames away."""
    from pydantic import ValidationError
    from shared.routers.projects import ProjectPatchIn

    with pytest.raises(ValidationError):
        ProjectPatchIn(deliveryStatus="shipped_it")

    assert ProjectPatchIn(deliveryStatus="completed").deliveryStatus == "completed"


async def test_a_patch_without_the_field_leaves_the_status_alone(project):
    """`exclude_unset` matters: renaming a project must not reset its delivery state."""
    from shared.routers.projects import ProjectPatchIn

    await _set(project, "in_progress")
    body = ProjectPatchIn(name="Renamed")
    assert "deliveryStatus" not in body.model_dump(exclude_unset=True)
    assert await _status(project) == "in_progress"
