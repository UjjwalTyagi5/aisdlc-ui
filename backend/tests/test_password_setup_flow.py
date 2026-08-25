"""Onboarding invite, forgot-password, and the set-password link they both use.

Accounts are created with NO password — `password_hash` is NULL — so the emailed
single-use link is not a recovery convenience, it is the ONLY way anybody ever signs in
for the first time. These tests cover that path and the properties that keep it from being
an account-takeover route: single use, expiry, no enumeration, and no password in an email.

Self-serve registration was removed at the same time; `test_register_endpoint_is_gone`
pins that, because a route coming back by merge is the realistic way it returns.

See the login/onboarding work of 2026-08-17.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.services import password_setup

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture(autouse=True)
def _capture_email(monkeypatch):
    """Intercept outbound email. Returns the list the tests assert against.

    Patched at the point of use in each router rather than on `email.send_email`, because
    both import the symbol directly.
    """
    sent: list[dict] = []

    async def _fake(to, subject, text_body, html_body=None):
        sent.append(
            {"to": to, "subject": subject, "text": text_body, "html": html_body or ""}
        )
        return True

    import shared.routers.auth_local as al
    import shared.routers.onboarding as ob

    monkeypatch.setattr(al, "send_email", _fake)
    monkeypatch.setattr(ob, "send_email", _fake)
    return sent


@pytest.fixture
async def org():
    org_id, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    admin = f"admin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Invite Test')"
        ), {"i": org_id, "s": f"inv-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": unit, "o": org_id})
    await grant_role(admin, org_id, "org_admin", tenant_id=org_id, scope_kind="organization")
    yield {"org": org_id, "unit": unit, "admin": admin}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _admin_hdr(org: dict) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=org["admin"], tenant_id=org["org"],
                              permissions=["admin:*"])
    }


def _onboard(c: TestClient, org: dict, email: str, role: str = "bu_admin"):
    return c.post(
        "/onboarding", headers=_admin_hdr(org),
        json={"email": email, "role": role, "workspaceId": org["unit"]},
    )


def _token_from(email_text: str) -> str:
    """Pull the token out of the link in the email body."""
    marker = "token="
    start = email_text.index(marker) + len(marker)
    return email_text[start:].split()[0].strip()


# ── the invite ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_onboarding_creates_an_account_with_no_password_and_emails_a_link(org, _capture_email):
    t = org
    email = f"nadia-{_uuid.uuid4().hex[:6]}@abcbank.com"

    r = _onboard(_client(), t, email)
    assert r.status_code == 201, r.text
    assert r.json()["invited"] is True

    async with get_db_session_superuser() as s:
        pw = (await s.execute(
            text("SELECT password_hash FROM users WHERE lower(email) = :e"), {"e": email}
        )).scalar()
    # NULL, not a hash of something random: the account states honestly that no password
    # has been chosen.
    assert pw is None

    assert len(_capture_email) == 1
    sent = _capture_email[0]
    assert sent["to"] == email
    assert "token=" in sent["text"]


@pytest.mark.asyncio
async def test_the_invite_email_contains_no_password(org, _capture_email):
    """The whole reason for the link design. A temp password in an inbox is a live
    credential sitting in a mailbox, a Sent folder, and every forward of the thread."""
    t = org
    email = f"omar-{_uuid.uuid4().hex[:6]}@abcbank.com"
    _onboard(_client(), t, email)

    body = _capture_email[0]["text"].lower()
    for word in ("temporary password", "your password is", "password:"):
        assert word not in body, f"invite email leaked a credential: {word!r}"


@pytest.mark.asyncio
async def test_the_link_sets_a_first_password_and_the_user_can_sign_in(org, _capture_email):
    t = org
    email = f"priya-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    token = _token_from(_capture_email[0]["text"])

    # Before: no password, so login is refused — with the uniform 401, not a message
    # confirming the account exists.
    assert c.post("/auth/login", json={"email": email, "password": "anything"}
                  ).status_code == 401

    assert c.get(f"/auth/reset-password/validate?token={token}").json()["status"] == "ok"
    r = c.post("/auth/reset-password",
               json={"token": token, "new_password": "Correct-Horse-9"})
    assert r.status_code == 200, r.text

    signed_in = c.post("/auth/login",
                       json={"email": email, "password": "Correct-Horse-9"})
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["token"]


@pytest.mark.asyncio
async def test_a_link_works_once(org, _capture_email):
    """Single use, enforced in the UPDATE that spends it, so two concurrent
    presentations cannot both win."""
    t = org
    email = f"sam-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    token = _token_from(_capture_email[0]["text"])

    assert c.post("/auth/reset-password",
                  json={"token": token, "new_password": "First-Choice-1"}).status_code == 200
    second = c.post("/auth/reset-password",
                    json={"token": token, "new_password": "Second-Choice-2"})
    assert second.status_code == 400
    assert c.get(f"/auth/reset-password/validate?token={token}").json()["status"] == "used"

    # And the first password is the one that stuck.
    assert c.post("/auth/login",
                  json={"email": email, "password": "First-Choice-1"}).status_code == 200


@pytest.mark.asyncio
async def test_an_expired_link_is_refused(org, _capture_email):
    t = org
    email = f"tara-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    token = _token_from(_capture_email[0]["text"])

    async with get_db_session_superuser() as s:
        await s.execute(
            text("UPDATE password_reset_tokens SET expires_at = :e"),
            {"e": datetime.now(tz=timezone.utc) - timedelta(minutes=1)},
        )

    assert c.get(f"/auth/reset-password/validate?token={token}").json()["status"] == "expired"
    assert c.post("/auth/reset-password",
                  json={"token": token, "new_password": "Too-Late-3"}).status_code == 400


@pytest.mark.asyncio
async def test_validating_a_link_does_not_spend_it(org, _capture_email):
    """A mail client that pre-fetches links must not burn the invite before the
    recipient clicks."""
    t = org
    email = f"umar-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    token = _token_from(_capture_email[0]["text"])

    for _ in range(3):
        assert c.get(f"/auth/reset-password/validate?token={token}").json()["status"] == "ok"
    assert c.post("/auth/reset-password",
                  json={"token": token, "new_password": "Still-Works-4"}).status_code == 200


@pytest.mark.asyncio
async def test_re_onboarding_an_existing_person_issues_no_new_link(org, _capture_email):
    """Otherwise placing somebody in a second unit would mint a set-password link for an
    account that already has a working password — a takeover route dressed as admin."""
    t = org
    email = f"vik-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    token = _token_from(_capture_email[0]["text"])
    c.post("/auth/reset-password", json={"token": token, "new_password": "Chosen-Pw-5"})
    _capture_email.clear()

    again = _onboard(c, t, email)
    assert again.status_code == 201, again.text
    assert again.json()["created"] is False
    assert again.json()["invited"] is False
    assert _capture_email == []


# ── forgot password ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forgot_password_emails_a_link_and_it_works(org, _capture_email):
    t = org
    email = f"wendy-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    c.post("/auth/reset-password",
           json={"token": _token_from(_capture_email[0]["text"]),
                 "new_password": "Original-Pw-6"})
    _capture_email.clear()

    assert c.post("/auth/forgot-password", json={"email": email}).status_code == 200
    assert len(_capture_email) == 1
    token = _token_from(_capture_email[0]["text"])

    assert c.post("/auth/reset-password",
                  json={"token": token, "new_password": "Replaced-Pw-7"}).status_code == 200
    assert c.post("/auth/login",
                  json={"email": email, "password": "Replaced-Pw-7"}).status_code == 200
    # The old one stops working, which is the point of a reset.
    assert c.post("/auth/login",
                  json={"email": email, "password": "Original-Pw-6"}).status_code == 401


@pytest.mark.asyncio
async def test_forgot_password_does_not_reveal_whether_an_account_exists(org, _capture_email):
    """This endpoint needs no password to probe with, so it is a sharper enumerator than
    login. Same status, same body, either way — and no email for the unknown address."""
    c = _client()
    known = f"xena-{_uuid.uuid4().hex[:6]}@abcbank.com"
    _onboard(c, org, known)
    _capture_email.clear()

    hit = c.post("/auth/forgot-password", json={"email": known})
    miss = c.post("/auth/forgot-password", json={"email": "nobody-at-all@abcbank.com"})

    assert hit.status_code == miss.status_code == 200
    assert hit.json() == miss.json()
    assert [m["to"] for m in _capture_email] == [known]


@pytest.mark.asyncio
async def test_a_second_request_retires_the_first_link(org, _capture_email):
    """Otherwise an earlier email — forwarded, or read over a shoulder — stays usable
    after the user has asked for a fresh one."""
    t = org
    email = f"yusuf-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    first = _token_from(_capture_email[0]["text"])
    _capture_email.clear()

    c.post("/auth/forgot-password", json={"email": email})
    second = _token_from(_capture_email[0]["text"])
    assert second != first

    assert c.get(f"/auth/reset-password/validate?token={first}").json()["status"] == "used"
    assert c.post("/auth/reset-password",
                  json={"token": first, "new_password": "Stale-Link-8"}).status_code == 400
    assert c.post("/auth/reset-password",
                  json={"token": second, "new_password": "Fresh-Link-9"}).status_code == 200


@pytest.mark.asyncio
async def test_a_deactivated_account_gets_no_reset_link(org, _capture_email):
    """It was switched off deliberately; a working link would be a way back in."""
    t = org
    email = f"zoe-{_uuid.uuid4().hex[:6]}@abcbank.com"
    c = _client()
    _onboard(c, t, email)
    _capture_email.clear()
    async with get_db_session_superuser() as s:
        await s.execute(
            text("UPDATE users SET active = false WHERE lower(email) = :e"), {"e": email}
        )

    assert c.post("/auth/forgot-password", json={"email": email}).status_code == 200
    assert _capture_email == []


# ── what was removed ─────────────────────────────────────────────────────────


def test_register_endpoint_is_gone():
    """Pinned because a deleted route is exactly the thing a merge quietly restores."""
    paths = {getattr(r, "path", "") for r in process_api.app.routes}
    assert "/auth/register" not in paths

    from process_api import _EXEMPT_PATHS
    assert "/auth/register" not in _EXEMPT_PATHS

    # 401, not 404: dropping the path from `_EXEMPT_PATHS` means the JWT middleware
    # refuses an unauthenticated caller before routing ever looks for a handler. That is
    # the stronger of the two answers — the endpoint is unreachable without a token AND
    # absent with one — so it is asserted as "not created", not as a specific code.
    r = _client().post(
        "/auth/register", json={"email": "x@abcbank.com", "password": "whatever12"}
    )
    assert r.status_code in (401, 404, 405), r.text
    assert r.status_code != 201


# ── the token store itself ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_raw_token_is_never_stored(org):
    """Only a SHA-256 hash is persisted, so a database dump yields no working links."""
    t = org
    user = f"u-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(
            text("INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, CAST(:t AS uuid))"),
            {"i": user, "e": f"{user}@abcbank.com", "t": t["org"]},
        )
        token = await password_setup.issue(s, user_id=user, purpose="reset", ttl_minutes=60)
        stored = (await s.execute(
            text("SELECT token_hash FROM password_reset_tokens WHERE user_id = :u"),
            {"u": user},
        )).scalar()

    assert stored != token
    assert len(stored) == 64  # sha256 hex
    assert token not in stored
