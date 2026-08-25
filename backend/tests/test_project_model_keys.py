"""Project-level BYOK (PRD §371/§1640/§1692/§1698) — a Project Admin may bring
their own model key, but only for a (provider, model_id) their Business Unit has
both (a) made reachable at all, and (b) explicitly opted into project-level keys
for via the allow_project_key policy. Live-DB tests (RLS-backed tables)."""
import uuid

import pytest

from tests.test_model_grants import _grant_provider, _seed_org_workspace_project, _seed_provider


async def _grant_and_allow_project_key(tenant: str, ws_id: str, provider_row: dict, model_id: str, allow: bool):
    from shared.services import model_grants as mg

    # get_bu_allowed (which assert_project_key_allowed reads through) now also
    # requires the PROVIDER itself to be granted to this BU
    # (integration_grants(kind='model_provider')), not just the model-level
    # org_model_grants/bu_model_key_policy rows below — see model_grants.py's
    # coupling fix. Every caller of this helper needs this regardless of
    # `allow`, since even the "blocked" tests exercise the reachability check
    # first.
    await _grant_provider(tenant, ws_id)

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": model_id, "credential_id": provider_row["id"],
          "visibility": "specific", "business_unit_ids": [ws_id]}],
        created_by="admin1",
    )
    await mg.set_bu_grants(
        tenant, ws_id,
        [{"provider": "anthropic", "model_id": model_id, "credential_id": provider_row["id"],
          "allow_project_key": allow}],
        updated_by="bu-admin",
    )


@pytest.mark.asyncio
async def test_assert_project_key_allowed_blocks_when_bu_policy_is_off():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"], workspace_id=ws_a)
    await _grant_and_allow_project_key(tenant, ws_a, provider, "claude-sonnet-4-6", allow=False)

    with pytest.raises(mg.ProjectKeyNotAllowedError):
        await mg.assert_project_key_allowed(tenant, proj_a, "anthropic", "claude-sonnet-4-6")


@pytest.mark.asyncio
async def test_assert_project_key_allowed_blocks_when_model_not_reachable_at_all():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    # No grant at all for this model — the BU can't reach it, let alone opt into project keys.
    with pytest.raises(mg.ProjectKeyNotAllowedError):
        await mg.assert_project_key_allowed(tenant, proj_a, "anthropic", "claude-sonnet-4-6")


@pytest.mark.asyncio
async def test_assert_project_key_allowed_passes_when_bu_opts_in():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"], workspace_id=ws_a)
    await _grant_and_allow_project_key(tenant, ws_a, provider, "claude-sonnet-4-6", allow=True)

    workspace_id = await mg.assert_project_key_allowed(tenant, proj_a, "anthropic", "claude-sonnet-4-6")
    assert workspace_id == ws_a


@pytest.mark.asyncio
async def test_project_can_onboard_and_select_its_own_key_end_to_end():
    """The real proof: a project's own key is (a) creatable once the BU opts in,
    (b) selectable even though its credential_id isn't one of the BU's shared ones,
    and (c) what resolve_model_for_run actually hands to the agent — not the BU key."""
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    from shared.services.model_resolver import resolve_model_for_run

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    bu_provider = await _seed_provider(tenant, ["claude-sonnet-4-6"], workspace_id=ws_a)
    await _grant_and_allow_project_key(tenant, ws_a, bu_provider, "claude-sonnet-4-6", allow=True)

    # Project onboards its OWN connection (mirrors what create_project_provider_route does).
    own_provider = await mc.create_provider(
        tenant, provider="anthropic", display_name="project-own-key",
        api_key="sk-project-own-secret", enabled_models=["claude-sonnet-4-6"],
        created_by="project-admin", workspace_id=ws_a, project_id=proj_a,
    )
    assert own_provider["project_id"] == proj_a

    # Must verify to 'valid' status to be resolvable — mirror the BU key's path.
    async def _fake_probe(*a, **k):
        return True
    import shared.services.model_config as mc_mod
    orig = mc_mod._probe_model
    mc_mod._probe_model = _fake_probe
    try:
        await mc.verify_provider(tenant, own_provider["id"])
        await mc.verify_provider(tenant, bu_provider["id"])
    finally:
        mc_mod._probe_model = orig

    selection = await mg.set_project_selection(
        tenant, proj_a,
        selected=[{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": own_provider["id"]}],
        default_key=None,
    )
    assert selection["selected"][0]["credential_id"] == own_provider["id"]

    resolved = await resolve_model_for_run(tenant, project_id=proj_a)
    assert resolved.api_key == "sk-project-own-secret"
    assert resolved.offering_id != bu_provider["offerings"][0]["id"]


@pytest.mark.asyncio
async def test_project_key_selection_rejected_for_unreachable_model():
    """A project can't select ITS OWN key for a model its BU never made reachable at
    all, even though the credential is genuinely the project's own — ownership alone
    isn't enough, the model must still be BU-reachable."""
    from shared.services import model_config as mc
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    # No grant/policy set up at all for this model.
    own_provider = await mc.create_provider(
        tenant, provider="anthropic", display_name="rogue-project-key",
        api_key="sk-x", enabled_models=["claude-sonnet-4-6"],
        created_by="project-admin", workspace_id=ws_a, project_id=proj_a,
    )
    with pytest.raises(mg.NotAllowedForUnitError):
        await mg.set_project_selection(
            tenant, proj_a,
            selected=[{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": own_provider["id"]}],
            default_key=None,
        )


@pytest.mark.asyncio
async def test_bu_key_policy_round_trips_through_allowed_bu():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"], workspace_id=ws_a)

    await _grant_and_allow_project_key(tenant, ws_a, provider, "claude-sonnet-4-6", allow=False)
    allowed = await mg.get_bu_allowed(tenant, ws_a)
    assert allowed[0]["allow_project_key"] is False

    await _grant_and_allow_project_key(tenant, ws_a, provider, "claude-sonnet-4-6", allow=True)
    allowed = await mg.get_bu_allowed(tenant, ws_a)
    assert allowed[0]["allow_project_key"] is True


# ---------------------------------------------------------------------------
# Router-level: scoped RBAC + end-to-end HTTP behavior for /model/project-providers.
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_project_provider_route_requires_bu_opt_in(mint_token):
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from shared.services import model_config as mc

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await mc.create_provider(
        tenant, provider="anthropic", display_name="bu-shared", api_key="sk-bu",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1", workspace_id=ws_a,
    )
    await _grant_and_allow_project_key(tenant, ws_a, provider, "claude-sonnet-4-6", allow=False)

    user = f"dev-{uuid.uuid4()}"
    await grant_role(user, proj_a, "developer", tenant_id=tenant, scope_kind="project")
    headers = {"Authorization": f"Bearer {mint_token(user_id=user, tenant_id=tenant, permissions=['run:create'])}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/model/project-providers",
            json={"projectId": proj_a, "provider": "anthropic", "displayName": "my-own-key",
                  "apiKey": "sk-mine", "enabledModels": ["claude-sonnet-4-6"]},
            headers=headers,
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "project_key_not_allowed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_project_provider_route_succeeds_when_allowed_and_scoped_to_caller(mint_token):
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from shared.services import model_config as mc

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await mc.create_provider(
        tenant, provider="anthropic", display_name="bu-shared-2", api_key="sk-bu",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1", workspace_id=ws_a,
    )
    await _grant_and_allow_project_key(tenant, ws_a, provider, "claude-sonnet-4-6", allow=True)

    owner = f"dev-owner-{uuid.uuid4()}"
    await grant_role(owner, proj_a, "developer", tenant_id=tenant, scope_kind="project")
    owner_headers = {"Authorization": f"Bearer {mint_token(user_id=owner, tenant_id=tenant, permissions=['run:create'])}"}

    outsider = f"dev-outsider-{uuid.uuid4()}"
    outsider_headers = {"Authorization": f"Bearer {mint_token(user_id=outsider, tenant_id=tenant, permissions=['run:create'])}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # An outsider with no role binding on this project must be denied (404, matches
        # the existing /model/allowed/project precedent — never confirm the project exists).
        denied = await client.post(
            "/model/project-providers",
            json={"projectId": proj_a, "provider": "anthropic", "displayName": "nope",
                  "apiKey": "sk-x", "enabledModels": ["claude-sonnet-4-6"]},
            headers=outsider_headers,
        )
        assert denied.status_code == 404

        created = await client.post(
            "/model/project-providers",
            json={"projectId": proj_a, "provider": "anthropic", "displayName": "my-own-key",
                  "apiKey": "sk-mine", "enabledModels": ["claude-sonnet-4-6"]},
            headers=owner_headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["projectId"] == proj_a
        assert "apiKey" not in body and "api_key" not in body  # key never crosses the boundary

        listed = await client.get(
            "/model/project-providers", params={"projectId": proj_a}, headers=owner_headers,
        )
        assert listed.status_code == 200
        assert any(p["id"] == body["id"] for p in listed.json())

        # The outsider can't even list it.
        listed_denied = await client.get(
            "/model/project-providers", params={"projectId": proj_a}, headers=outsider_headers,
        )
        assert listed_denied.status_code == 404

        deleted = await client.delete(
            f"/model/project-providers/{body['id']}", params={"projectId": proj_a}, headers=owner_headers,
        )
        assert deleted.status_code == 204
