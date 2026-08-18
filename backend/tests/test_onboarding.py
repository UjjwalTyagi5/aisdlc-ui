"""Onboarding: admit, place, and hand over.

THE THIRD ACT IS WHAT THE TESTS ARE REALLY FOR. Creating an account and binding a role
are easy to get right and easy to verify. Raising the `role_assignment` request is the
one that makes the flow a HANDOVER rather than a half-finished admission — without it
somebody lands in a unit, sits on the read-only floor, and nothing anywhere says why.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.services import governance_requests as governance
from shared.services import notifications

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    org_id, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    admin_id = f"admin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Onboard Test')"
        ), {"i": org_id, "s": f"onb-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": bu, "o": org_id})
    # The admin gets a REAL org_admin binding, not just a token claiming admin:*.
    # `assert_can_grant_role` re-resolves the caller's permissions from the database
    # rather than reading them off the token — deliberately, so a token issued before
    # a demotion cannot mint a durable grant — and a fixture that only forged the
    # claim was testing a caller who does not exist in production.
    from shared.authz.grant import grant_role
    await grant_role(admin_id, org_id, "org_admin",
                     tenant_id=org_id, scope_kind="organization")
    yield {"org": org_id, "bu": bu, "admin": admin_id}


def _headers(uid: str, org_id: str, perms: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=uid, tenant_id=org_id, permissions=perms)}


def _admin(org: dict) -> dict:
    return _headers(org["admin"], org["org"], ["admin:*"])


@pytest.mark.asyncio
async def test_onboarding_a_contributor_hands_the_role_decision_over(org):
    c = TestClient(process_api.app)
    email = f"amara-{_uuid.uuid4().hex[:6]}@abcbank.com"

    # The unit needs an admin for there to be anybody to hand the decision TO. The
    # notification addresses a ROLE, so emitting it into a unit with nobody in that role
    # puts an obligation on no one — which the endpoint now reports as
    # `notifiedBusinessUnitAdmin: false` rather than pretending somebody was told.
    from shared.authz.grant import grant_role
    bu_admin = f"buadmin-{_uuid.uuid4()}"
    await grant_role(bu_admin, org["bu"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")

    # A second unit with its own admin, who must hear nothing about this. The
    # notification names a role AND a unit; before it named only the role, and this
    # person read the other unit's joiners.
    other_bu, other_admin = str(_uuid.uuid4()), f"buadmin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'lending', 'Lending')"
        ), {"i": other_bu, "o": org["org"]})
    await grant_role(other_admin, other_bu, "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")

    r = c.post("/onboarding", headers=_admin(org),
               json={"email": email, "role": "contributor", "workspaceId": org["bu"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True
    assert body["notifiedBusinessUnitAdmin"] is True
    assert body["roleRequestId"], "the handover request is the point of the flow"

    # The account exists and is bound to the unit.
    async with get_db_session_superuser() as s:
        assert (await s.execute(
            text("SELECT 1 FROM users WHERE lower(email) = :e"), {"e": email}
        )).first() is not None
    async with get_db_session_for_tenant(org["org"]) as s:
        binding = (await s.execute(
            text("SELECT role_name, scope_kind FROM role_bindings WHERE user_id = :u"),
            {"u": body["identityId"]},
        )).first()
        assert (binding.role_name, binding.scope_kind) == ("contributor", "business_unit")

        # And the obligation is sitting with the unit's admin.
        req = await governance.get_request(s, body["roleRequestId"])
        assert req["type"] == "role_assignment"
        assert req["currentApproverRole"] == "bu_admin"
        assert req["targetRef"] == body["identityId"]

        # Who is also told about it — THIS unit's admin, and only theirs.
        bell = await notifications.list_for(s, user_id=bu_admin, role="bu_admin")
        assert any(n["kind"] == "member_awaiting_role" for n in bell)
        assert await notifications.list_for(s, user_id=other_admin, role="bu_admin") == []


@pytest.mark.asyncio
async def test_onboarding_a_unit_admin_raises_no_request(org):
    """They were given their job by this act — there is nothing left to hand over."""
    c = TestClient(process_api.app)
    r = c.post("/onboarding", headers=_admin(org),
               json={"email": f"farah-{_uuid.uuid4().hex[:6]}@abcbank.com",
                     "role": "bu_admin", "workspaceId": org["bu"]})
    assert r.status_code == 201, r.text
    assert r.json()["roleRequestId"] is None


@pytest.mark.asyncio
async def test_a_contributor_with_no_unit_is_refused(org):
    """They would belong to nobody: no admin is prompted for their role, so they sit
    with no access and nothing to explain why."""
    c = TestClient(process_api.app)
    r = c.post("/onboarding", headers=_admin(org),
               json={"email": "nowhere@abcbank.com", "role": "contributor"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_only_the_two_org_level_roles_are_accepted(org):
    """The real gate, not the dialog's. An Org Admin does not decide what people do
    inside a unit, so a request naming a working role is refused however it arrives."""
    c = TestClient(process_api.app)
    r = c.post("/onboarding", headers=_admin(org),
               json={"email": "dev@abcbank.com", "role": "developer", "workspaceId": org["bu"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_role"


@pytest.mark.asyncio
async def test_a_unit_admin_cannot_onboard(org):
    """Admitting someone to the ORGANISATION is org-wide authority. member:manage is
    a BU Admin's, and they assign roles inside their unit — they do not decide who
    belongs to the organisation."""
    c = TestClient(process_api.app)
    r = c.post(
        "/onboarding",
        headers=_headers(f"bua-{_uuid.uuid4()}", org["org"], ["artifact:view", "member:manage"]),
        json={"email": "x@abcbank.com", "role": "contributor", "workspaceId": org["bu"]},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_re_onboarding_an_existing_person_places_them_rather_than_failing(org):
    c = TestClient(process_api.app)
    email = f"repeat-{_uuid.uuid4().hex[:6]}@abcbank.com"
    headers = _admin(org)

    first = c.post("/onboarding", headers=headers,
                   json={"email": email, "role": "contributor", "workspaceId": org["bu"]})
    assert first.json()["created"] is True

    second = c.post("/onboarding", headers=headers,
                    json={"email": email, "role": "contributor", "workspaceId": org["bu"]})
    assert second.status_code == 201, second.text
    # Same person, and the caller can say "added to Payments" rather than "invited".
    assert second.json()["created"] is False
    assert second.json()["identityId"] == first.json()["identityId"]


@pytest.mark.asyncio
async def test_a_new_account_cannot_be_signed_into_until_a_password_is_set(org):
    """There is no invite-email path yet, so the account exists and is unusable —
    which is the honest state. A weak placeholder would be an account anybody could
    guess their way into."""
    c = TestClient(process_api.app)
    email = f"nopass-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c.post("/onboarding", headers=_admin(org),
           json={"email": email, "role": "contributor", "workspaceId": org["bu"]})

    for attempt in ("", "password", "changeme", email):
        r = c.post("/auth/login", json={"email": email, "password": attempt or "x"})
        assert r.status_code == 401, f"{attempt!r} should not sign in"


@pytest.mark.asyncio
async def test_an_unknown_business_unit_is_not_found(org):
    c = TestClient(process_api.app)
    r = c.post("/onboarding", headers=_admin(org),
               json={"email": "x@abcbank.com", "role": "contributor",
                     "workspaceId": str(_uuid.uuid4())})
    assert r.status_code == 404
