"""Two approval effects that reported success and granted nothing the requester could see.

Both were found live on 2026-09-03 by a BU Admin raising requests against the PAYMENTS
unit and an Org Admin approving them.

The `org` fixture and `_bind` helper are reused from test_governance_requests.py rather
than re-declared — the tenant/unit/project shape they build is exactly what these
effects read, and a second copy would be one more thing to keep in step.
"""
import json
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant

# Imported for use as fixtures in this module. `org` must be in this module's namespace
# for pytest to resolve it; `_bind` is a plain helper.
from tests.test_governance_requests import _bind, org  # noqa: F401

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


def _token(user_id: str, tenant: str, perms: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=tenant, permissions=perms,
    )}


async def test_org_admin_approving_a_project_connector_request_also_grants_the_unit(org):
    """Reported live: a BU Admin asked for Slack on their project, it routed to the
    Organization Admin, and Approve failed with "This project's business unit has not
    been given slack. An Organization Admin has to grant it to the unit first."

    The Org Admin *is* that person, on a request routed to them precisely because they
    hold that authority — the refusal handed them their own permission back as a
    blocker, and the request could not be approved by anyone. Approving now grants the
    unit, then narrows onto the project.

    Deliberately NO integration_grants seed: its absence is the condition under test
    (contrast test_connector_access_request_grants_on_approval, which seeds it).
    """
    bu_admin = f"bua-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    c = TestClient(process_api.app)
    raised = c.post(
        "/governance-approvals",
        headers=_token(bu_admin, org["org"], ["artifact:view", "run:create"]),
        json={
            "type": "connector_access", "title": "Slack access",
            "description": "Requesting Slack for our work.", "priority": "normal",
            "workspaceId": org["bu"], "projectId": org["project"],
            "targetId": "slack", "accessLevel": "write",
        },
    )
    assert raised.status_code == 201, raised.text

    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide",
        headers=_token(f"org-{_uuid.uuid4()}", org["org"], ["admin:*"]),
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(org["org"]) as s:
        unit = (await s.execute(text(
            "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            " AND workspace_id = CAST(:w AS uuid) AND kind = 'connector' AND target_ref = 'slack'"
        ), {"t": org["org"], "w": org["bu"]})).scalar()
        assert unit == 1, "approving did not give the unit the connector"

        proj = (await s.execute(text(
            "SELECT 1 FROM project_connector_access WHERE tenant_id = CAST(:t AS uuid) "
            " AND project_id = CAST(:p AS uuid) AND kind = 'connector' AND target_ref = 'slack'"
        ), {"t": org["org"], "p": org["project"]})).scalar()
        assert proj == 1, "approving did not narrow the connector onto the project"


async def test_approving_a_model_request_grants_the_model_not_just_its_provider(org):
    """Reported live: "bedrock/ap-southeast-3/deepseek.v3.2 access" was approved and the
    BU Admin's Models page still did not list it.

    The effect wrote only the provider grant (`integration_grants`) and dropped the
    requested `modelId`. The Models page reads `org_model_grants` — a different table,
    and both layers must say yes — so the requester was told their model was approved
    and then could not find it anywhere.
    """
    bu_admin = f"bua-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    c = TestClient(process_api.app)
    raised = c.post(
        "/governance-approvals",
        headers=_token(bu_admin, org["org"], ["artifact:view", "run:create"]),
        json={
            "type": "model_provider_access",
            "title": "bedrock/ap-southeast-3/deepseek.v3.2 access",
            "description": "Needed for the ledger work.", "priority": "normal",
            "workspaceId": org["bu"],
            "providerModel": {
                "provider": "bedrock", "modelId": "bedrock/ap-southeast-3/deepseek.v3.2",
            },
        },
    )
    assert raised.status_code == 201, raised.text

    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide",
        headers=_token(f"org-{_uuid.uuid4()}", org["org"], ["admin:*"]),
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(org["org"]) as s:
        provider = (await s.execute(text(
            "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            " AND workspace_id = CAST(:w AS uuid) AND kind = 'model_provider' "
            "   AND target_ref = 'bedrock'"
        ), {"t": org["org"], "w": org["bu"]})).scalar()
        assert provider == 1, "the provider grant regressed"

        row = (await s.execute(text(
            "SELECT business_unit_ids FROM org_model_grants "
            " WHERE tenant_id = CAST(:t AS uuid) AND provider = 'bedrock' "
            "   AND model_id = 'bedrock/ap-southeast-3/deepseek.v3.2'"
        ), {"t": org["org"]})).first()
    assert row is not None, "the approved MODEL never reached org_model_grants"
    units = row.business_unit_ids
    if isinstance(units, str):
        units = json.loads(units)
    assert org["bu"] in [str(u) for u in units], "granted, but not to the asking unit"


async def test_a_provider_only_request_grants_the_provider_and_curates_nothing(org):
    """The Models page asks for a PROVIDER, with no model named — ~2,700 per-model
    "Request access" buttons asked for something the approval screen could not give.

    Approving grants the provider and stops there: which of its models the unit gets
    stays the Organization Admin's curation, which is the boundary chosen for this
    flow. The provider grant alone is what unblocks that curation.
    """
    bu_admin = f"bua-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    c = TestClient(process_api.app)
    raised = c.post(
        "/governance-approvals",
        headers=_token(bu_admin, org["org"], ["artifact:view", "run:create"]),
        json={
            "type": "model_provider_access", "title": "Amazon Bedrock access",
            "description": "Requesting the Bedrock provider.", "priority": "normal",
            "workspaceId": org["bu"],
            "providerModel": {"provider": "bedrock"},
        },
    )
    assert raised.status_code == 201, raised.text

    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide",
        headers=_token(f"org-{_uuid.uuid4()}", org["org"], ["admin:*"]),
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(org["org"]) as s:
        provider = (await s.execute(text(
            "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            " AND workspace_id = CAST(:w AS uuid) AND kind = 'model_provider' "
            "   AND target_ref = 'bedrock'"
        ), {"t": org["org"], "w": org["bu"]})).scalar()
        assert provider == 1, "a provider-only request did not grant the provider"

        curated = (await s.execute(text(
            "SELECT count(*) FROM org_model_grants WHERE tenant_id = CAST(:t AS uuid) "
            " AND provider = 'bedrock'"
        ), {"t": org["org"]})).scalar()
    assert curated == 0, "a provider-only request must not curate models by itself"


async def test_approving_registers_a_provider_connection_so_it_has_a_card(org):
    """The grant needs something to be granted ON.

    Reported live: OpenAI was requested, approved, and the Org Admin's Models grid was
    unchanged — no OpenAI card, nothing explaining why. The grant lands in
    `integration_grants` while that grid renders `model_providers` CONNECTIONS, so the
    approval wrote a grant against a provider with no connection to hang it on.
    Approving now performs the same keyless registration the "Add provider" button does.

    Keyless by design: the approver does not necessarily hold a credential. The card
    exists with the requesting unit already granted; adding a key and curating models
    are the remaining steps.
    """
    bu_admin = f"bua-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    async with get_db_session_for_tenant(org["org"]) as s:
        before = (await s.execute(text(
            "SELECT count(*) FROM model_providers WHERE tenant_id = CAST(:t AS uuid) "
            " AND provider = 'openai'"
        ), {"t": org["org"]})).scalar()
    assert before == 0, "fixture already had an openai connection"

    c = TestClient(process_api.app)
    raised = c.post(
        "/governance-approvals",
        headers=_token(bu_admin, org["org"], ["artifact:view", "run:create"]),
        json={
            "type": "model_provider_access", "title": "OpenAI access",
            "description": "Requesting the OpenAI provider.", "priority": "normal",
            "workspaceId": org["bu"], "providerModel": {"provider": "openai"},
        },
    )
    assert raised.status_code == 201, raised.text
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide",
        headers=_token(f"org-{_uuid.uuid4()}", org["org"], ["admin:*"]),
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(org["org"]) as s:
        conn = (await s.execute(text(
            "SELECT secret_ref FROM model_providers WHERE tenant_id = CAST(:t AS uuid) "
            " AND provider = 'openai'"
        ), {"t": org["org"]})).first()
    assert conn is not None, "approving did not register a connection — no card to grant on"
    assert conn.secret_ref is None, "registration must stay keyless; the approver holds no key"


async def test_approving_twice_does_not_register_a_second_connection(org):
    """Idempotent: a second approval for the same provider must not add a duplicate card."""
    bu_admin = f"bua-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    c = TestClient(process_api.app)

    for _ in range(2):
        raised = c.post(
            "/governance-approvals",
            headers=_token(bu_admin, org["org"], ["artifact:view", "run:create"]),
            json={
                "type": "model_provider_access", "title": "OpenAI access",
                "description": "Requesting the OpenAI provider.", "priority": "normal",
                "workspaceId": org["bu"], "providerModel": {"provider": "openai"},
            },
        )
        assert raised.status_code == 201, raised.text
        decided = c.post(
            f"/governance-approvals/{raised.json()['id']}/decide",
            headers=_token(f"org-{_uuid.uuid4()}", org["org"], ["admin:*"]),
            json={"decision": "approve"},
        )
        assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(org["org"]) as s:
        count = (await s.execute(text(
            "SELECT count(*) FROM model_providers WHERE tenant_id = CAST(:t AS uuid) "
            " AND provider = 'openai'"
        ), {"t": org["org"]})).scalar()
    assert count == 1, f"expected one openai connection, found {count}"


async def test_approving_a_model_keeps_the_units_existing_models(org):
    """The additive property. `set_bu_grants` REPLACES a unit's set, so reaching for that
    obvious helper would have made each approval silently revoke every model approved
    before it."""
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO org_model_grants (id, tenant_id, provider, model_id, visibility, "
            "  business_unit_ids, created_by) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), 'anthropic', 'claude-opus-4-5', "
            "  'specific', :bus, 'test')"
        ), {"i": str(_uuid.uuid4()), "t": org["org"], "bus": json.dumps([org["bu"]])})

    bu_admin = f"bua-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    c = TestClient(process_api.app)
    raised = c.post(
        "/governance-approvals",
        headers=_token(bu_admin, org["org"], ["artifact:view", "run:create"]),
        json={
            "type": "model_provider_access", "title": "a second model",
            "description": "second one.", "priority": "normal", "workspaceId": org["bu"],
            "providerModel": {
                "provider": "bedrock", "modelId": "bedrock/ap-southeast-3/deepseek.v3.2",
            },
        },
    )
    assert raised.status_code == 201, raised.text
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide",
        headers=_token(f"org-{_uuid.uuid4()}", org["org"], ["admin:*"]),
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(org["org"]) as s:
        kept = (await s.execute(text(
            "SELECT business_unit_ids FROM org_model_grants "
            " WHERE tenant_id = CAST(:t AS uuid) AND model_id = 'claude-opus-4-5'"
        ), {"t": org["org"]})).first()
    assert kept is not None, "approving one model deleted the unit's existing grant row"
    units = kept.business_unit_ids
    if isinstance(units, str):
        units = json.loads(units)
    assert org["bu"] in [str(u) for u in units], "the unit lost a model it already had"
