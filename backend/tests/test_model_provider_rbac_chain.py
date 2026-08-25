"""Task 6 — full RBAC-chain integration test (spec §6).

Walks the entire chain for real, end to end: Org Admin grants a provider to a BU
via the NEW `integration_grants(kind='model_provider')` mechanism (Tasks 1-3) ->
BU Admin adds a key (Task 4's ownership+grant-gated, key-required BU-scoped
creation) -> BU Admin assigns it to a project (Task 5) -> resolve_model_for_run
(backend/shared/services/model_resolver.py, UNMODIFIED by this whole plan)
resolves that exact offering for that project. Plus the negative case: an
ungranted BU cannot add a key directly via the API — the bug the user opened
this whole redesign conversation to fix.

Mirrors the httpx.AsyncClient + mint_token + _seed_org_workspace_project pattern
used throughout tests/test_model_config_api.py, tests/test_model_provider_assign.py
and tests/test_model_grants.py.
"""
import uuid

import pytest

from tests.test_model_grants import _seed_org_workspace_project

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


def _headers(mint_token, user_id: str, tenant_id: str, permissions: list[str]) -> dict:
    return {"Authorization": f"Bearer {mint_token(user_id=user_id, tenant_id=tenant_id, permissions=permissions)}"}


@pytest.mark.asyncio
async def test_full_chain_org_grant_to_run_resolution(mint_token, monkeypatch):
    """Org Admin grants -> BU Admin keys+verifies -> BU Admin assigns to project ->
    resolve_model_for_run resolves that exact offering, restricted to what this
    project was actually granted+assigned (proven via a distractor offering that
    is valid+enabled tenant-wide but never assigned to this project — without this,
    the resolve assertions below would trivially pass even if the whole chain were
    a no-op, since a fresh tenant with zero org_model_grants rows resolves openly)."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    from shared.services.model_resolver import ModelNotEnabledError, resolve_model_for_run

    tenant = str(uuid.uuid4())
    ws_id, proj_id = await _seed_org_workspace_project(tenant, "Payments")

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = _headers(mint_token, org_admin, tenant, ["admin:*"])

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = _headers(mint_token, bu_admin, tenant, ["model:manage"])

    # A Project Admin bound directly to the project (run:create, project scope) —
    # distinct from the BU Admin, matching real role separation (project_admin
    # role catalog: member:manage/project:*/model:manage/run:create/... per
    # shared/authz/permissions.py).
    project_admin = f"project-admin-{uuid.uuid4()}"
    await grant_role(project_admin, proj_id, "project_admin", tenant_id=tenant, scope_kind="project")
    proj_headers = _headers(mint_token, project_admin, tenant, ["run:create"])

    async def _probe_ok(provider, model, api_key, api_base=None):
        return True

    monkeypatch.setattr(mc, "_probe_model", _probe_ok)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Org Admin grants Payments access to anthropic — the NEW
        # integration_grants(kind='model_provider') mechanism (Tasks 1-3), not the
        # old org_model_grants curation table.
        grant_resp = await client.put(
            "/model/providers/grants",
            params={"workspaceId": ws_id},
            json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        assert grant_resp.status_code == 200, grant_resp.text

        # 2. BU Admin adds a key — Task 4's ownership+grant-gated, key-required path.
        created = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Payments prod", "api_key": "sk-test-123",
                "models": [{"model_id": "claude-sonnet-4-6"}], "workspaceId": ws_id,
            },
            headers=bu_headers,
        )
        assert created.status_code == 201, created.text
        provider_id = created.json()["id"]
        offering_id = created.json()["offerings"][0]["id"]

        # A real user click-path verifies ("Test" button) before the key is truly
        # usable — resolve_model_for_run only loads offerings whose provider
        # status='valid' (freshly created providers start 'unverified').
        verify_resp = await client.post(f"/model/providers/{provider_id}/verify", headers=bu_headers)
        assert verify_resp.status_code == 200 and verify_resp.json()["status"] == "valid", verify_resp.text

        # Distractor: a second, tenant-wide, valid+enabled offering that is NEVER
        # assigned to proj_id. Also gives the tenant an org_model_grants row, which
        # flips effective_project_offerings out of its "no grants configured yet ->
        # stay fully open" backward-compat shortcut (model_grants.py) and into
        # actually filtering by this project's own `selected` set.
        distractor = await mc.create_provider(
            tenant, provider="openai", display_name="Unrelated org key", api_key="sk-other",
            enabled_models=["gpt-4o"], created_by="admin1",
        )
        await mc.verify_provider(tenant, distractor["id"])
        distractor_offering_id = distractor["offerings"][0]["id"]
        await mg.set_org_grants(
            tenant,
            [{"provider": "openai", "model_id": "gpt-4o", "credential_id": None,
              "visibility": "global", "business_unit_ids": []}],
            created_by="admin1",
        )

        # 3. BU Admin assigns the key to the project — Task 5.
        assign_resp = await client.post(
            f"/model/providers/{provider_id}/assign", json={"projectId": proj_id}, headers=bu_headers,
        )
        assert assign_resp.status_code == 200, assign_resp.text
        selected = assign_resp.json()["selected"]
        assert any(
            e["provider"] == "anthropic" and e["model_id"] == "claude-sonnet-4-6"
            and e["credentialId"] == provider_id
            for e in selected
        ), selected

        # 4. Project Admin opens Settings -> Model and tries to set it as defaultKey.
        get_resp = await client.get("/model/allowed/project", params={"projectId": proj_id}, headers=proj_headers)
        assert get_resp.status_code == 200, get_resp.text
        put_resp = await client.put(
            "/model/allowed/project",
            params={"projectId": proj_id},
            json={"selected": get_resp.json()["selected"], "defaultKey": provider_id},
            headers=proj_headers,
        )
        # FIXED by Task 12 (was a KNOWN GAP found by this capstone test, see
        # task-6-report.md): PUT /model/allowed/project (set_project_selection,
        # shared/services/model_grants.py) re-validates the ENTIRE echoed-back
        # `selected` array against org_model_grants-derived allowed_keys/own_keys.
        # Previously, neither recognized an entry whose credential_id points at a
        # BU-scoped (workspace_id-only) model_providers row pushed via
        # assign_provider_to_project (Task 5): allowed_keys never contains it
        # (Org Admin's NEW provider-grant flow never writes org_model_grants), and
        # own_keys' `_project_owned_offering_keys` filtered on the exact
        # project_id — this connection's project_id is NULL (it's BU-scoped). So a
        # Project Admin could not successfully call this endpoint once their BU
        # Admin had assigned them a key — confirmed by direct investigation (a
        # standalone repro against shared/services/model_grants.py), not a fixture
        # bug. Task 12 extended `_project_owned_offering_keys` to also match a
        # model_providers row whose workspace_id equals the project's own
        # workspace_id, so this now succeeds and defaultKey is actually set.
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["defaultKey"] == provider_id, put_resp.json()

    # 5. resolve_model_for_run resolves that exact offering — unmodified by this
    # plan (backend/shared/services/model_resolver.py). `defaultKey` is never read
    # anywhere in the resolver (confirmed by inspection) — a real call site threads
    # the caller's chosen offering_id explicitly, which is what we do here. This
    # holds regardless of the step-4 gap above: resolution is driven entirely by
    # `ProjectModelSelection.selected` (populated directly by assign_provider_to_
    # project in step 3) via effective_project_offerings, never by `defaultKey`.
    resolved = await resolve_model_for_run(tenant, offering_id=offering_id, project_id=proj_id)
    assert resolved.offering_id == offering_id
    assert resolved.provider == "anthropic"

    # The distractor offering is tenant-wide valid+enabled but was never assigned
    # to this project — proves the project's OWN selection (populated by assign,
    # Task 5) is what actually gates resolution, not merely "anything enabled
    # anywhere in the tenant" (which would make the assertions above vacuous).
    with pytest.raises(ModelNotEnabledError):
        await resolve_model_for_run(tenant, offering_id=distractor_offering_id, project_id=proj_id)


@pytest.mark.asyncio
async def test_ungranted_bu_cannot_add_a_key_directly(mint_token):
    """The bug the user opened this whole design conversation to fix: a BU Admin
    who holds the tenant-wide `model:manage` permission but whose business unit has
    NO `model_provider` grant at all must still be denied when adding a key
    directly via the API — pre-Task-4 code let this through with a 201 for any
    tenant-wide model:manage holder, regardless of any grant.

    Already covered at the unit level by test_model_config_api.py::
    test_bu_scoped_provider_creation_requires_grant. Reasserting the same
    invariant here deliberately, at the end-to-end capstone level and through this
    file's own from-scratch client/fixture setup (no grant of any kind is ever
    written for this tenant/workspace) — not a silent duplicate.
    """
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role

    tenant = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant, "Lending")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = _headers(mint_token, bu_admin, tenant, ["model:manage"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Should be blocked", "api_key": "sk-test-123",
                "models": [{"model_id": "claude-sonnet-4-6"}], "workspaceId": ws_id,
            },
            headers=bu_headers,
        )
    assert resp.status_code == 403, resp.text
