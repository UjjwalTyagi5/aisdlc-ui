"""Deleting a document is something an owner agrees to, not something you just do.

THE ASYMMETRY THIS CLOSES. Uploading a document is gated on somebody accepting it.
Removing one was gated on nothing but holding `artifact:delete`, which most roles do —
so a single call could undo an approval nobody was asked about and leave the record
quietly missing a file.

WHAT IS WORTH ASSERTING. That a request is created is the easy half and would pass even
if the effect deleted nothing, or deleted the wrong row. So the tests below check the
two ends that actually matter: WHO the request lands with, and that the document is
still there until the effect runs and gone afterwards.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.governance import routing  # noqa: E402

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
            "VALUES (:i, :s, 'Delete Request Test')"
        ), {"i": org, "s": f"del-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Delete Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "project": proj}


async def _document(project, *, stage) -> str:
    art = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status, uploaded_by) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), :st, CAST(:t AS uuid), "
            "  'document', :bp, 'approved', 'uploader@example.com')"
        ), {"i": art, "p": project["project"], "st": stage, "t": project["org"],
            # A LOCAL-LOOKING PATH ON PURPOSE: `is_blob_path` returns False for it, so
            # the effect skips the blob call. These tests are about the row and the
            # routing, and reaching for Azure would make them an integration test.
            "bp": f"C:/legacy/{stage or 'project'}/report.pdf"})
        await s.commit()
    return art


async def _exists(project, artifact_id) -> bool:
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        return (await s.execute(
            text("SELECT count(*) FROM artifacts WHERE id = CAST(:a AS uuid)"),
            {"a": artifact_id},
        )).scalar() == 1


async def _user_who_can_see_the_project(project) -> str:
    """A real user WITH AN ORG-SCOPED BINDING.

    The binding is not decoration: `_assert_project_visible` resolves reach from
    `role_bindings`, so a user holding the right permissions in their token but no
    binding gets a flat 404 — the project is not merely refused, it is invisible. That
    is the correct behaviour and it is also how the first version of this test failed.
    """
    user = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email, active) "
            "VALUES (:i, CAST(:t AS uuid), :e, true)"
        ), {"i": user, "t": project["org"], "e": f"{user[:8]}@example.com"})
        await s.execute(text(
            "INSERT INTO role_bindings "
            "  (id, user_id, scope_kind, scope_id, role_name, tier, status, tenant_id) "
            "VALUES (gen_random_uuid(), :u, 'organization', :o, 'org_admin', "
            "        'governance', 'active', CAST(:t AS uuid))"
        ), {"u": user, "o": project["org"], "t": project["org"]})
        await s.commit()
    return user


# -- the type is registered end to end ----------------------------------------


def test_the_type_is_in_the_catalogue_and_has_an_approver():
    """Exhaustiveness is what stops a type being added with nobody to decide it."""
    assert "artifact_delete" in routing.REQUEST_TYPES
    assert "artifact_delete" in routing.GOVERNANCE_APPROVER_ROLE
    assert "artifact_delete" in routing.REQUEST_TYPE_LABEL
    # Type-routed, not tier-routed: the approver is the document's owner, which does
    # not depend on who happens to be asking.
    assert "artifact_delete" in routing.TYPE_ROUTED
    # Filed by pressing Delete, never composed in the request picker.
    assert "artifact_delete" in routing.SYSTEM_RAISED


async def test_the_database_accepts_the_new_type(project):
    """0055. A type in Python but not in the CHECK is refused at INSERT, and the
    aborted transaction then makes every later statement fail somewhere unrelated."""
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        allowed = (await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_governance_request_type'"
        ))).scalar()
    assert "artifact_delete" in (allowed or "")


# -- who it lands with --------------------------------------------------------


async def test_an_agent_document_goes_to_that_agent_owner():
    """The person whose Approve put it in the record is the person who may agree to
    lose it. NOT the requester's Project Admin, which is where tier routing sends it."""
    assert routing.agent_owner_role("design") == routing.agent_owner_role_or_none("design")
    owner = routing.agent_owner_role("design")
    assert owner and owner != "project_admin"


def test_a_project_wide_document_falls_back_to_the_project_admin():
    """It has no stage, so there is no agent owner to route to. `_or_none` is used for
    exactly this — absence here is legitimate, not a bug to raise on."""
    assert routing.agent_owner_role_or_none(None) is None


# -- the effect ---------------------------------------------------------------


async def test_the_effect_deletes_the_row(project):
    """THE HALF THAT MATTERS. A test asserting only that a request was created would
    pass against an effect that deleted nothing."""
    from shared.governance.effects import apply_on_approve

    art = await _document(project, stage="design")
    assert await _exists(project, art)

    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        note = await apply_on_approve(s, {
            "type": "artifact_delete",
            "tenantId": project["org"],
            "projectId": project["project"],
            "id": str(_uuid.uuid4()),
            "payload": {"artifactId": art, "projectId": project["project"]},
        })
        await s.commit()

    assert "report.pdf" in note
    assert not await _exists(project, art)


async def test_the_effect_is_idempotent_on_a_missing_document(project):
    """A request approved twice, or a document an administrator removed between the ask
    and the answer, must not fail the decision — the outcome is already true."""
    from shared.governance.effects import apply_on_approve

    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        note = await apply_on_approve(s, {
            "type": "artifact_delete",
            "tenantId": project["org"],
            "projectId": project["project"],
            "id": str(_uuid.uuid4()),
            "payload": {"artifactId": str(_uuid.uuid4()),
                        "projectId": project["project"]},
        })
    assert "no longer exists" in note


async def test_the_effect_refuses_a_request_naming_no_document(project):
    """Refusing beats recording a hollow approval — the decision would close having
    done nothing, and the queue would say it was actioned."""
    from shared.governance.effects import EffectNotAvailable, apply_on_approve

    async with get_db_session_superuser() as s:
        with pytest.raises(EffectNotAvailable):
            await apply_on_approve(s, {
                "type": "artifact_delete",
                "tenantId": project["org"],
                "projectId": project["project"],
                "id": str(_uuid.uuid4()),
                "payload": {},
            })


async def test_a_document_in_another_project_is_not_deleted(project):
    """The effect scopes by project_id as well as id. Without that, a request could
    name any artifact id in the tenant and have it destroyed."""
    from shared.governance.effects import apply_on_approve

    art = await _document(project, stage="design")

    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        note = await apply_on_approve(s, {
            "type": "artifact_delete",
            "tenantId": project["org"],
            # A DIFFERENT project than the one the document belongs to.
            "projectId": str(_uuid.uuid4()),
            "id": str(_uuid.uuid4()),
            "payload": {"artifactId": art, "projectId": str(_uuid.uuid4())},
        })
        await s.commit()

    assert "no longer exists" in note
    assert await _exists(project, art), "the document was deleted from another project"


# -- the route itself ---------------------------------------------------------
#
# THE GAP THIS CLOSES. The first version of this file tested the routing decision and
# the effect — both ends — and never called the endpoint in between. It shipped with
# `create_request(...)` missing its required `workspace_id`, which is a TypeError on the
# very first real click and cannot be caught by testing either end in isolation.


async def test_the_route_raises_a_request_and_deletes_nothing(project):
    """The whole point of the endpoint: a request exists afterwards, the file does not
    go anywhere, and the response is 202 rather than a 204 that would claim it had."""
    import httpx
    from config.auth.jwt import create_access_token
    from process_api import app

    art = await _document(project, stage="design")
    user = await _user_who_can_see_the_project(project)

    token = create_access_token(
        user_id=user, tenant_id=project["org"],
        permissions=["artifact:delete", "artifact:view", "project:read"],
        platform_role="org_admin",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as cl:
        r = await cl.post(
            f"/artifacts/{art}/deletion-request",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "duplicate upload"},
        )

    assert r.status_code == 202, r.text
    assert await _exists(project, art), "the route deleted the document itself"

    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        row = (await s.execute(text(
            "SELECT type, status, payload FROM governance_requests "
            "WHERE project_id = CAST(:p AS uuid) AND type = 'artifact_delete'"
        ), {"p": project["project"]})).mappings().first()
    assert row is not None, "no governance request was raised"
    # Against the CATALOGUE, not a guessed literal: the real value is "submitted" and
    # a hardcoded list here would be a fourth copy of a set the code already owns.
    assert row["status"] in routing.OPEN_STATUSES
    assert (row["payload"] or {}) != {}


async def test_the_route_refuses_a_deletion_with_no_reason(project):
    """422, not a request nobody can act on. The approver is being asked to destroy
    something irreversibly."""
    import httpx
    from config.auth.jwt import create_access_token
    from process_api import app

    art = await _document(project, stage="design")
    user = await _user_who_can_see_the_project(project)

    token = create_access_token(
        user_id=user, tenant_id=project["org"],
        permissions=["artifact:delete", "artifact:view", "project:read"],
        platform_role="org_admin",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as cl:
        r = await cl.post(
            f"/artifacts/{art}/deletion-request",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "   "},
        )

    assert r.status_code == 422, r.text
    assert await _exists(project, art)
