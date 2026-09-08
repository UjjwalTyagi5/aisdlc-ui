"""The publication routes, over real HTTP, with real tokens and real role bindings.

WHY OVER HTTP RATHER THAN CALLING THE SERVICE. The service tests
(`test_artifact_publication.py`) prove the decision logic. They cannot prove the thing
most likely to be wrong on a gated route: that the AUTHZ is wired up. A route whose
permission dependency resolves the wrong stage, or which forgot project scoping, has
passing service tests and a hole.

THE CASE THAT MATTERS MOST is `test_a_stage_owner_cannot_publish_another_stage`: the
approve permissions are per stage precisely so an Architect cannot sign off a
deployment, and nothing but an end-to-end call proves the path parameter is what
selects the permission.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import process_api  # noqa: E402
from config.auth.jwt import create_access_token  # noqa: E402
from shared.authz.grant import grant_role  # noqa: E402
from shared.db import (  # noqa: E402
    get_db_session_for_tenant, get_db_session_superuser,
)

pytestmark = pytest.mark.usefixtures("purge_created_orgs")

BASE = "/artifact-versions"
PRODUCER = "producer@example.com"


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project():
    org, unit, project = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Publication Route Test')"
        ), {"i": org, "s": f"pubr-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Pub Route Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "unit": unit, "project": project}


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=org, permissions=perms)}


async def _member(t, role: str, perms: list[str]):
    """A real user with a real project-scoped binding and a token carrying `perms`."""
    user = f"{role}-{_uuid.uuid4()}"
    await grant_role(user, t["project"], role, tenant_id=t["org"], scope_kind="project")
    return user, _hdr(user, t["org"], perms)


async def _seed_draft(t, stage: str, payload=None, producer=PRODUCER) -> int:
    """A draft version, created directly — snapshotting is not what these test."""
    from shared.services.artifact_versions import snapshot_stage_payload

    async with get_db_session_for_tenant(t["org"]) as s:
        ref = await snapshot_stage_payload(
            s, tenant_id=t["org"], project_id=t["project"], stage=stage,
            payload=payload if payload is not None else {"x": 1}, produced_by=producer)
        await s.commit()
    return ref.version


# -- the happy path -----------------------------------------------------------


async def test_the_stage_owner_can_publish(org_project):
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "architect", ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(f"{BASE}/{t['project']}/stages/design/versions/{v}/publish", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"


async def test_a_published_version_is_then_readable_as_the_published_one(org_project):
    t = org_project
    v = await _seed_draft(t, "design", payload={"c4": "signed"})
    _, hdr = await _member(t, "architect", ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        c.post(f"{BASE}/{t['project']}/stages/design/versions/{v}/publish", headers=hdr)
        r = c.get(f"{BASE}/{t['project']}/stages/design/versions/published", headers=hdr)
    assert r.status_code == 200
    assert r.json()["payload"] == {"c4": "signed"}


async def test_nothing_published_reads_as_null_not_as_the_draft(org_project):
    """THE FALLBACK THAT MUST NOT EXIST. A draft answer here would make the gate
    decorative before phase 3 ever reads it."""
    t = org_project
    await _seed_draft(t, "design")
    _, hdr = await _member(t, "architect", ["artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.get(f"{BASE}/{t['project']}/stages/design/versions/published", headers=hdr)
    assert r.status_code == 200 and r.json() is None


# -- the authz that only an end-to-end call proves ----------------------------


async def test_a_stage_owner_cannot_publish_another_stage(org_project):
    """THE ONE THAT MATTERS. Permissions are per stage so an Architect cannot sign off
    a deployment. Only the path parameter selects which permission is demanded, and
    nothing short of a real request proves that wiring."""
    t = org_project
    v = await _seed_draft(t, "deployment")
    # Holds design's approval, not deployment's.
    _, hdr = await _member(t, "architect", ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/deployment/versions/{v}/publish", headers=hdr)
    assert r.status_code == 403, r.text


async def test_the_producer_cannot_publish_their_own_version(org_project):
    """Holding the permission is not enough — the run gate and the deployment gate
    both hold this line, verified live, and so must this."""
    t = org_project
    user = f"architect-{_uuid.uuid4()}"
    await grant_role(user, t["project"], "architect",
                     tenant_id=t["org"], scope_kind="project")
    v = await _seed_draft(t, "design", producer=user)
    hdr = _hdr(user, t["org"], ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(f"{BASE}/{t['project']}/stages/design/versions/{v}/publish", headers=hdr)
    assert r.status_code == 403, r.text
    assert "cannot also publish" in r.text


async def test_a_track_agent_stage_is_refused(org_project):
    """`data_engineering` has an owner in the catalogue but no backend agent and no
    approve permission. Fail closed."""
    t = org_project
    _, hdr = await _member(t, "architect", ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/data_engineering/versions/1/publish",
            headers=hdr)
    assert r.status_code == 403


async def test_the_ui_phase_name_is_refused(org_project):
    """`review` is the UI spelling; the backend stage is `code_review`. Accepting it
    would gate on a permission that does not exist."""
    t = org_project
    _, hdr = await _member(t, "architect", ["artifact:approve_code_review", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(f"{BASE}/{t['project']}/stages/review/versions/1/publish", headers=hdr)
    assert r.status_code == 403


async def test_reading_needs_only_artifact_view(org_project):
    """Seeing what exists is not approving it — a reader without any approve
    permission must still be able to list."""
    t = org_project
    await _seed_draft(t, "design")
    _, hdr = await _member(t, "developer", ["artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.get(f"{BASE}/{t['project']}/stages/design/versions", headers=hdr)
    assert r.status_code == 200 and len(r.json()) == 1


async def test_a_caller_from_another_project_is_refused(org_project):
    """Project scope is the second check. Holding `artifact:approve_design` somewhere
    in the tenant must not reach THIS project's versions."""
    t = org_project
    v = await _seed_draft(t, "design")
    outsider = f"outsider-{_uuid.uuid4()}"
    hdr = _hdr(outsider, t["org"], ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(f"{BASE}/{t['project']}/stages/design/versions/{v}/publish", headers=hdr)
    assert r.status_code in (403, 404), r.text


# -- rejection ----------------------------------------------------------------


async def test_rejecting_requires_a_reason(org_project):
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "architect", ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(f"{BASE}/{t['project']}/stages/design/versions/{v}/reject",
                   headers=hdr, json={"reason": ""})
    assert r.status_code == 422, r.text


async def test_rejecting_records_the_reason(org_project):
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "architect", ["artifact:approve_design", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(f"{BASE}/{t['project']}/stages/design/versions/{v}/reject",
                   headers=hdr, json={"reason": "no ADRs"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert r.json()["rejectionReason"] == "no ADRs"


# -- the response shape -------------------------------------------------------


async def test_the_list_response_does_not_ship_payloads(org_project):
    """A list view does not need them, they can be large, and named fields keep the
    surface from widening the day somebody adds a column."""
    t = org_project
    await _seed_draft(t, "design", payload={"secret_ish": "big blob"})
    _, hdr = await _member(t, "developer", ["artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.get(f"{BASE}/{t['project']}/stages/design/versions", headers=hdr)

    # Assert the preconditions SEPARATELY. Indexing straight into the body turned an
    # empty list into an IndexError that named nothing — this failed once in a long
    # combined run and the traceback could not say whether the route had leaked a
    # payload or simply returned nothing.
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list) and len(body) == 1, (
        f"expected exactly one version, got {body!r}"
    )
    assert "payload" not in body[0]
    assert "secret_ish" not in r.text


def test_the_list_response_model_has_no_payload_field():
    """The structural half of the test above, and the one that actually guarantees it.

    A data-driven assertion only proves the payload was absent for THAT row; this
    proves the list route cannot carry one at all, because `response_model=VersionOut`
    filters anything the model does not declare. `VersionDetailOut` adds `payload` and
    is used only on the single-version reads, where it is asked for.
    """
    from shared.routers.artifact_versions import VersionDetailOut, VersionOut

    assert "payload" not in VersionOut.model_fields
    assert "payload" in VersionDetailOut.model_fields


# -- phase 4: asking for what the gate refuses --------------------------------


async def _seed_published(t, stage: str, payload=None) -> int:
    from shared.services.artifact_versions import publish_version, snapshot_stage_payload

    async with get_db_session_for_tenant(t["org"]) as s:
        ref = await snapshot_stage_payload(
            s, tenant_id=t["org"], project_id=t["project"], stage=stage,
            payload=payload or {"x": 1}, produced_by=PRODUCER)
        await publish_version(
            s, tenant_id=t["org"], project_id=t["project"], stage=stage,
            version=ref.version, published_by="owner@example.com")
        await s.commit()
    return ref.version


async def test_a_draft_can_be_requested_and_routes_to_the_stages_owner(org_project):
    """THE ESCALATION PATH. `run:create`, not the approve permission — ASKING is not
    deciding — and the approver is the owner of the PRODUCING stage, not the
    requester's Project Admin, who never had standing to judge the design."""
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "developer", ["run:create", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/design/versions/{v}/request-access",
            headers=hdr,
            json={"consumerStage": "security", "reason": "needed for a spike"})
    assert r.status_code == 200, r.text
    assert r.json()["approverRole"] == "architect"
    assert r.json()["requestId"]


async def test_requesting_a_published_version_is_refused(org_project):
    """Filing a request that grants what the caller already has teaches an approver
    that these are noise."""
    t = org_project
    v = await _seed_published(t, "design")
    _, hdr = await _member(t, "developer", ["run:create", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/design/versions/{v}/request-access",
            headers=hdr, json={"consumerStage": "security", "reason": "x"})
    assert r.status_code == 409, r.text
    assert "already published" in r.text


async def test_a_request_must_carry_a_reason(org_project):
    """The owner is being asked to vouch for unfinished work; "please approve" is not
    something they can weigh."""
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "developer", ["run:create", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/design/versions/{v}/request-access",
            headers=hdr, json={"consumerStage": "security", "reason": ""})
    assert r.status_code == 422


async def test_an_unknown_consumer_stage_is_refused(org_project):
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "developer", ["run:create", "artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/design/versions/{v}/request-access",
            headers=hdr, json={"consumerStage": "not_a_stage", "reason": "x"})
    assert r.status_code == 404


async def test_a_reader_without_run_create_cannot_file_one(org_project):
    """Viewing the rules is not the same as being an agent that hit them."""
    t = org_project
    v = await _seed_draft(t, "design")
    _, hdr = await _member(t, "developer", ["artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.post(
            f"{BASE}/{t['project']}/stages/design/versions/{v}/request-access",
            headers=hdr, json={"consumerStage": "security", "reason": "x"})
    assert r.status_code == 403


# -- phase 5: the matrix over HTTP --------------------------------------------


async def test_the_matrix_is_readable_with_only_artifact_view(org_project):
    """A matrix only approvers could read would leave everyone else discovering the
    rules by being refused."""
    t = org_project
    await _seed_published(t, "testing")
    await _seed_draft(t, "design")
    _, hdr = await _member(t, "developer", ["artifact:view"])

    with TestClient(process_api.app) as c:
        r = c.get(f"{BASE}/{t['project']}/matrix", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {x["stage"]: x for x in body["stages"]}

    assert body["enforced"] is False          # default; the UI must say so
    assert rows["testing"]["publishedVersion"] == 1
    assert rows["testing"]["consumers"]["deployment"] == "open"
    assert rows["design"]["publishedVersion"] is None
    assert rows["design"]["consumers"]["security"] == "needs_request"
    assert rows["design"]["ownerRole"] == "architect"
    assert rows["design"]["consumers"]["design"] == "self"
