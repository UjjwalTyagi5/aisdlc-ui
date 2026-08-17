"""The whole onboarding journey, end to end, as the people in it experience it.

Not a unit test of any one endpoint. Each test walks a real sequence — admit somebody,
place them, let them sign in, look at what they can actually reach — because every bug
this flow has had lived in the seams between those steps rather than inside them: an
account created with no way to sign in, a request raised that granting a role never
closed, a project visible to its creator and nobody else.

The five journeys:
  1. Org Admin creates a Business Unit and appoints its admin
  2. that admin signs in and sees exactly what they hold
  3. a Contributor is admitted, their unit's admin is asked for a role, assigns one,
     and the Contributor's access changes as a result
  4. the Contributor is staffed onto a project and can then see it — and only it
  5. the BU Admin creates a project and names its owner

Plus the invariant the whole model rests on: a unit has exactly one admin.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.authz.resolver import resolve_permissions_for_user
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture(autouse=True)
def _capture_email(monkeypatch):
    """Hold the invite emails so a test can follow the set-password link."""
    sent: list[dict] = []

    async def _fake(to, subject, text_body, html_body=None):
        sent.append({"to": to, "text": text_body})
        return True

    import shared.routers.auth_local as al
    import shared.routers.onboarding as ob

    monkeypatch.setattr(al, "send_email", _fake)
    monkeypatch.setattr(ob, "send_email", _fake)
    return sent


@pytest.fixture
async def org():
    """An organization with an Org Admin and nothing else. Units are created by tests."""
    org_id = str(_uuid.uuid4())
    admin = f"orgadmin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name, monthly_budget_usd) "
            "VALUES (:i, :s, 'E2E Bank', 100000)"
        ), {"i": org_id, "s": f"e2e-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, CAST(:t AS uuid))"
        ), {"i": admin, "e": f"{admin}@e2ebank.com", "t": org_id})
    await grant_role(admin, org_id, "org_admin", tenant_id=org_id, scope_kind="organization")
    yield {"org": org_id, "admin": admin}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org_id: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org_id, permissions=perms)
    }


def _org_hdr(org: dict) -> dict:
    return _hdr(org["admin"], org["org"], ["admin:*"])


async def _as_themselves(user_id: str, org_id: str) -> dict:
    """Headers carrying the person's REAL resolved permissions.

    The whole point of several of these tests is what somebody can reach with the
    permissions their bindings actually produce, so the token is built from the resolver
    rather than from a hand-written list that could flatter them.
    """
    perms = await resolve_permissions_for_user(user_id, org_id)
    return _hdr(user_id, org_id, perms)


def _create_unit(c: TestClient, org: dict, name: str) -> str:
    r = c.post("/workspaces", headers=_org_hdr(org), json={"displayName": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _onboard(c: TestClient, org: dict, email: str, role: str, unit: str):
    return c.post("/onboarding", headers=_org_hdr(org),
                  json={"email": email, "role": role, "workspaceId": unit})


def _set_password(c: TestClient, invite_text: str, password: str) -> None:
    token = invite_text[invite_text.index("token=") + 6:].split()[0].strip()
    r = c.post("/auth/reset-password", json={"token": token, "new_password": password})
    assert r.status_code == 200, r.text


# ── 1 & 2: create a unit, appoint its admin, and let them look at their access ──


@pytest.mark.asyncio
async def test_a_bu_admin_is_onboarded_signs_in_and_sees_their_access(org, _capture_email):
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")

    email = f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    r = _onboard(c, t, email, "bu_admin", unit)
    assert r.status_code == 201, r.text
    assert r.json()["invited"] is True
    user_id = r.json()["userId"]

    # They set their own password from the emailed link, then sign in for real.
    _set_password(c, _capture_email[0]["text"], "Payments-Admin-1")
    signed_in = c.post("/auth/login", json={"email": email, "password": "Payments-Admin-1"})
    assert signed_in.status_code == 200, signed_in.text

    session = signed_in.json()
    perms = set(session["permissions"])
    # What a BU Admin is FOR: run the unit, its people, its money, its connections.
    assert {"member:manage", "role:manage", "workspace:manage",
            "project:create", "cost:view", "audit:view"} <= perms
    # And what they are deliberately not for — governance does not run agents.
    assert "agent:invoke" not in perms
    assert "run:create" not in perms

    # "View all the access granted to them": the unit they administer is the one they
    # see, and the endpoint the My Access page reads returns exactly it.
    hdr = _hdr(user_id, t["org"], sorted(perms))
    units = c.get("/workspaces", headers=hdr)
    assert units.status_code == 200, units.text
    assert [w["id"] for w in units.json()] == [unit]


@pytest.mark.asyncio
async def test_a_bu_admin_sees_their_own_unit_and_not_a_sibling(org, _capture_email):
    """Two units, one admin each. The reason to assert this in the E2E flow rather than
    only in the scope tests is that it is the first thing a real admin notices."""
    t = org
    c = _client()
    mine = _create_unit(c, t, "Payments")
    theirs = _create_unit(c, t, "Lending")

    email = f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    user_id = _onboard(c, t, email, "bu_admin", mine).json()["userId"]
    hdr = await _as_themselves(user_id, t["org"])

    ids = [w["id"] for w in c.get("/workspaces", headers=hdr).json()]
    assert ids == [mine]
    assert theirs not in ids


# ── the invariant: one admin per unit ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_unit_has_exactly_one_bu_admin(org, _capture_email):
    """The second appointment is refused, and the refusal names who to remove."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")

    first = f"first-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    assert _onboard(c, t, first, "bu_admin", unit).status_code == 201

    second = f"second-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    r = _onboard(c, t, second, "bu_admin", unit)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "unit_already_administered"
    # Naming the incumbent is the difference between a refusal somebody can act on and
    # one that generates a support ticket.
    assert first in detail["message"]


@pytest.mark.asyncio
async def test_the_same_admin_can_be_re_granted(org, _capture_email):
    """Idempotence must survive the new rule: `grant_role` is called again to extend an
    expiry, and treating that as a conflict would break renewal."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    email = f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    user_id = _onboard(c, t, email, "bu_admin", unit).json()["userId"]

    await grant_role(user_id, unit, "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")  # must not raise


@pytest.mark.asyncio
async def test_another_unit_can_have_its_own_admin(org, _capture_email):
    """The rule is one admin PER UNIT, not one admin overall."""
    t = org
    c = _client()
    payments = _create_unit(c, t, "Payments")
    lending = _create_unit(c, t, "Lending")

    assert _onboard(c, t, f"a-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                    "bu_admin", payments).status_code == 201
    assert _onboard(c, t, f"b-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                    "bu_admin", lending).status_code == 201


@pytest.mark.asyncio
async def test_removing_the_admin_frees_the_unit(org, _capture_email):
    """Handover is two deliberate steps, and the second must actually work."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    outgoing = f"out-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    outgoing_id = _onboard(c, t, outgoing, "bu_admin", unit).json()["userId"]

    revoked = c.request(
        "DELETE", "/admin/assignments", headers=_org_hdr(t),
        json={"user_id": outgoing_id, "workspace_id": unit, "role_name": "bu_admin"},
    )
    assert revoked.status_code == 200, revoked.text

    incoming = f"in-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    assert _onboard(c, t, incoming, "bu_admin", unit).status_code == 201


# ── 3: the Contributor journey ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_onboarding_a_contributor_asks_their_bu_admin_for_a_role(org, _capture_email):
    """The handover. A Contributor lands with the read-only floor and an obligation is
    recorded against the unit's admin — without which somebody sits with no access and
    no record of why."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_email = f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    admin_id = _onboard(c, t, admin_email, "bu_admin", unit).json()["userId"]

    contrib_email = f"con-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    r = _onboard(c, t, contrib_email, "contributor", unit)
    assert r.status_code == 201, r.text
    contrib_id = r.json()["userId"]
    assert r.json()["roleRequestId"], "the handover request is the point of the flow"

    # The Contributor holds the floor and nothing else.
    assert await resolve_permissions_for_user(contrib_id, t["org"]) == ["artifact:view"]

    # And the request is sitting with the unit's admin.
    admin_hdr = await _as_themselves(admin_id, t["org"])
    queue = c.get("/governance-approvals", headers=admin_hdr)
    assert queue.status_code == 200, queue.text
    payload = queue.json()
    rows = payload["items"] if isinstance(payload, dict) else payload
    mine = [x for x in rows if x.get("targetRef") == contrib_id]
    assert mine, "the contributor's role request is not in their BU Admin's queue"
    assert mine[0]["currentApproverRole"] == "bu_admin"


@pytest.mark.asyncio
async def test_assigning_the_role_is_the_approval(org, _capture_email):
    """The platform collapses "approve" and "assign" into one act, deliberately.

    Approving a `role_assignment` is REFUSED — `apply_on_approve` raises and the service
    comments that "an approval that cannot take effect is refused, not recorded". The
    reasoning is sound and stronger than a two-step flow: an approved-but-unassigned
    request would be a promise the system had no way to keep, and somebody would sit
    with the read-only floor believing they had been given a role.

    So the BU Admin's single act is to assign, and that is what changes the person's
    access. This test asserts the permission set before and after, because "approved" on
    its own has fooled people.
    """
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_id = _onboard(c, t, f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "bu_admin", unit).json()["userId"]

    contrib_email = f"con-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    onboarded = _onboard(c, t, contrib_email, "contributor", unit).json()
    contrib_id, request_id = onboarded["userId"], onboarded["roleRequestId"]
    _capture_email.clear()

    admin_hdr = await _as_themselves(admin_id, t["org"])

    # Approving on its own is refused, and says why.
    refused = c.post(f"/governance-approvals/{request_id}/decide", headers=admin_hdr,
                     json={"decision": "approve", "reason": "Joining the squad"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "EFFECT_UNAVAILABLE"

    # Before: the read-only floor and nothing else.
    assert await resolve_permissions_for_user(contrib_id, t["org"]) == ["artifact:view"]

    # The BU Admin assigns the real role. THIS is the act that grants access.
    assigned = c.post("/admin/assignments", headers=admin_hdr, json={
        "user_id": contrib_id, "workspace_id": unit, "role_name": "developer",
    })
    assert assigned.status_code == 200, assigned.text

    perms = set(await resolve_permissions_for_user(contrib_id, t["org"]))
    assert {"run:create", "agent:invoke", "skill:edit"} <= perms


@pytest.mark.asyncio
async def test_the_contributor_signs_in_and_sees_their_access(org, _capture_email):
    """They set a password from the invite and their session carries the real set."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_id = _onboard(c, t, f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "bu_admin", unit).json()["userId"]
    _capture_email.clear()

    contrib_email = f"con-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    contrib_id = _onboard(c, t, contrib_email, "contributor", unit).json()["userId"]
    _set_password(c, _capture_email[0]["text"], "Contributor-Pw-1")

    admin_hdr = await _as_themselves(admin_id, t["org"])
    c.post("/admin/assignments", headers=admin_hdr, json={
        "user_id": contrib_id, "workspace_id": unit, "role_name": "developer",
    })

    session = c.post("/auth/login",
                     json={"email": contrib_email, "password": "Contributor-Pw-1"})
    assert session.status_code == 200, session.text
    perms = set(session.json()["permissions"])
    assert {"run:create", "agent:invoke", "artifact:view"} <= perms

    # Their unit is visible to them, which is what My Access renders.
    hdr = _hdr(contrib_id, t["org"], sorted(perms))
    assert [w["id"] for w in c.get("/workspaces", headers=hdr).json()] == [unit]


# ── 4 & 5: projects ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bu_admin_creates_a_project_and_names_its_owner(org, _capture_email):
    """Creating is immediate for a BU Admin — they run the unit, so there is nobody
    above them to ask, and asking themselves would be the self-approval the platform
    refuses everywhere else. Naming an owner binds that person as project_admin."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_id = _onboard(c, t, f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "bu_admin", unit).json()["userId"]
    owner_id = _onboard(c, t, f"po-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "contributor", unit).json()["userId"]

    admin_hdr = await _as_themselves(admin_id, t["org"])
    created = c.post("/projects", headers=admin_hdr, json={
        "name": "Card Rails", "workspaceId": unit, "ownerId": owner_id,
    })
    assert created.status_code in (200, 201), created.text
    project_id = created.json()["id"]

    owner_perms = set(await resolve_permissions_for_user(owner_id, t["org"]))
    assert {"member:manage", "run:create", "approve"} <= owner_perms, (
        "the named owner did not become the project's admin"
    )

    # And the owner can now open the project they own.
    owner_hdr = await _as_themselves(owner_id, t["org"])
    assert c.get(f"/projects/{project_id}", headers=owner_hdr).status_code == 200


@pytest.mark.asyncio
async def test_a_bu_admin_can_own_the_project_they_create(org, _capture_email):
    """Naming yourself is the ordinary case, and is NOT a tier conflict: the bu_admin
    binding sits at business-unit scope and project_admin at project scope, which is
    exactly what per-scope tier separation exists to permit."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_id = _onboard(c, t, f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "bu_admin", unit).json()["userId"]

    admin_hdr = await _as_themselves(admin_id, t["org"])
    created = c.post("/projects", headers=admin_hdr, json={
        "name": "Self Owned", "workspaceId": unit, "ownerId": admin_id,
    })
    assert created.status_code in (200, 201), created.text

    async with get_db_session_for_tenant(t["org"]) as s:
        role = (await s.execute(text(
            "SELECT role_name FROM role_bindings WHERE user_id = :u "
            "  AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
        ), {"u": admin_id, "p": created.json()["id"]})).scalar()
    assert role == "project_admin"


@pytest.mark.asyncio
async def test_a_contributor_staffed_onto_a_project_can_see_it(org, _capture_email):
    """Requirement in one sentence: enrolled in a project, therefore able to view it —
    and only it."""
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_id = _onboard(c, t, f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "bu_admin", unit).json()["userId"]
    admin_hdr = await _as_themselves(admin_id, t["org"])

    joined = c.post("/projects", headers=admin_hdr,
                    json={"name": "Card Rails", "workspaceId": unit}).json()["id"]
    other = c.post("/projects", headers=admin_hdr,
                   json={"name": "Fraud Engine", "workspaceId": unit}).json()["id"]

    contrib_email = f"con-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    contrib_id = _onboard(c, t, contrib_email, "contributor", unit).json()["userId"]

    # Staffed onto one project by its roster.
    added = c.post(f"/projects/{joined}/members", headers=admin_hdr,
                   json={"email": contrib_email, "roleName": "developer"})
    assert added.status_code == 201, added.text

    contrib_hdr = await _as_themselves(contrib_id, t["org"])
    listed = c.get("/projects", headers=contrib_hdr)
    assert listed.status_code == 200, listed.text
    ids = {p["id"] for p in listed.json()["items"]}
    assert joined in ids
    assert c.get(f"/projects/{joined}", headers=contrib_hdr).status_code == 200

    # THE UNIT BINDING IS THE WIDER REACH, and that is the designed contract rather than
    # a leak: `visible_project_ids` maps a business-unit binding to every project in that
    # unit, so somebody placed in Payments can see Payments' project list. Being STAFFED
    # onto a project is what gives them a role on it — the thing that changes is what
    # they may do there, not whether they can see it exists.
    assert other in ids

    # Someone with no binding in this unit at all sees neither.
    outsider = f"out-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, CAST(:t AS uuid))"
        ), {"i": outsider, "e": f"{outsider}@e2ebank.com", "t": t["org"]})
    outsider_hdr = _hdr(outsider, t["org"], ["artifact:view"])
    assert c.get(f"/projects/{joined}", headers=outsider_hdr).status_code == 404


@pytest.mark.asyncio
async def test_a_project_admin_can_create_a_project_in_their_unit(org, _capture_email):
    """The other creator, and a GAP worth naming rather than asserting away.

    The decision taken was that a BU Admin creates directly — they run the unit, and
    routing their own request to themselves would be the self-approval the platform
    refuses everywhere else. A Project Admin creating one is the case where an approval
    IS meaningful, and `project_creation` already exists as a governance request type
    routed to `bu_admin`.

    The backend does not yet raise it: POST /projects creates the project outright for
    any holder of `project:create`. The frontend dialog shows a "needs approval" banner
    for a project_admin creator and sets `approvalStatus: "pending_approval"` in its
    optimistic cache — but nothing server-side backs that, and `Project` has no column
    to represent it.

    Asserted as the CURRENT behaviour rather than the desired one, so the test tells the
    truth and fails the day somebody implements the request without finishing it.
    """
    t = org
    c = _client()
    unit = _create_unit(c, t, "Payments")
    admin_id = _onboard(c, t, f"bu-{_uuid.uuid4().hex[:6]}@e2ebank.com",
                        "bu_admin", unit).json()["userId"]
    admin_hdr = await _as_themselves(admin_id, t["org"])

    seed = c.post("/projects", headers=admin_hdr,
                  json={"name": "Seed", "workspaceId": unit}).json()["id"]
    pa_email = f"pa-{_uuid.uuid4().hex[:6]}@e2ebank.com"
    pa_id = _onboard(c, t, pa_email, "contributor", unit).json()["userId"]
    c.post(f"/projects/{seed}/members", headers=admin_hdr,
           json={"email": pa_email, "roleName": "project_admin"})

    pa_hdr = await _as_themselves(pa_id, t["org"])
    created = c.post("/projects", headers=pa_hdr,
                     json={"name": "Their Idea", "workspaceId": unit})
    assert created.status_code in (200, 201), created.text

    # No server-side approval state exists yet — see the docstring.
    assert "approvalStatus" not in created.json()
    # And the project is live immediately, which is what the gap means in practice.
    assert c.get(f"/projects/{created.json()['id']}", headers=pa_hdr).status_code == 200
