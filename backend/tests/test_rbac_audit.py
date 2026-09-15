"""RBAC writes and refusals leave a trail, and the trail cannot be rewritten.

Granting and revoking roles are the highest-leverage writes on the platform and wrote
nothing to audit_events until now: "who gave this person org_admin, and when" had no
answer. These tests assert the record exists, names the actor separately from the
subject, and lands in the same transaction as the change it describes.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.audit import (
    ACCESS_DENIED,
    RBAC_ROLE_GRANTED,
    RBAC_ROLE_REVOKED,
)
from shared.authz.grant import grant_role, revoke_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    org_id, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Audit Test')"
        ), {"i": org_id, "s": f"audit-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org_id})
    yield {"org": org_id, "bu": bu}


async def _events(org_id: str, event_type: str | None = None) -> list[dict]:
    async with get_db_session_for_tenant(org_id) as s:
        sql = "SELECT actor_id, event_type, resource_id, payload FROM audit_events"
        params: dict = {}
        if event_type:
            sql += " WHERE event_type = :et"
            params["et"] = event_type
        rows = (await s.execute(text(sql + " ORDER BY created_at"), params)).fetchall()
    return [
        {"actor_id": r.actor_id, "event_type": r.event_type,
         "resource_id": r.resource_id, "payload": r.payload}
        for r in rows
    ]


@pytest.mark.asyncio
async def test_grant_is_audited_with_actor_and_subject(org):
    subject = f"subject-{_uuid.uuid4()}"
    actor = f"actor-{_uuid.uuid4()}"
    await grant_role(
        subject, org["bu"], "developer", tenant_id=org["org"],
        scope_kind="business_unit", granted_by=actor,
    )

    events = await _events(org["org"], RBAC_ROLE_GRANTED)
    assert len(events) == 1, events
    e = events[0]
    # Actor and subject are different people, and that difference is the whole point.
    assert e["actor_id"] == actor
    assert e["resource_id"] == subject
    assert e["payload"]["role"] == "developer"
    assert e["payload"]["scope_kind"] == "business_unit"
    assert e["payload"]["scope_id"] == org["bu"]


@pytest.mark.asyncio
async def test_revoke_is_audited_only_when_something_was_removed(org):
    """Revocation is idempotent; a repeat that removes nothing must not be recorded."""
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit")

    await revoke_role(
        subject, org["bu"], "qa", tenant_id=org["org"],
        scope_kind="business_unit", revoked_by="admin-1",
    )
    assert len(await _events(org["org"], RBAC_ROLE_REVOKED)) == 1

    # Second revoke deletes nothing — the trail must not gain a phantom entry.
    await revoke_role(
        subject, org["bu"], "qa", tenant_id=org["org"],
        scope_kind="business_unit", revoked_by="admin-1",
    )
    assert len(await _events(org["org"], RBAC_ROLE_REVOKED)) == 1


@pytest.mark.asyncio
async def test_system_grants_record_a_null_actor(org):
    """Startup seeding has no human actor — NULL is a meaningful value, not a gap."""
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(subject, org["bu"], "ba", tenant_id=org["org"], scope_kind="business_unit")
    events = await _events(org["org"], RBAC_ROLE_GRANTED)
    assert events[0]["actor_id"] is None


@pytest.mark.asyncio
async def test_expiry_is_captured_on_the_record(org):
    from datetime import datetime, timedelta, timezone

    subject = f"subject-{_uuid.uuid4()}"
    expires = datetime.now(tz=timezone.utc) + timedelta(hours=2)
    await grant_role(
        subject, org["bu"], "project_admin", tenant_id=org["org"],
        scope_kind="business_unit", expires_at=expires, granted_by="admin-1",
    )
    events = await _events(org["org"], RBAC_ROLE_GRANTED)
    # A time-bound elevation is exactly the grant asked about afterwards.
    assert events[0]["payload"]["expires_at"] is not None


@pytest.mark.asyncio
async def test_denials_are_audited(org):
    """The 403 trail: one refusal is noise, forty in a minute is the only warning."""
    token = create_access_token(
        user_id="denied-user", tenant_id=org["org"], permissions=["artifact:view"]
    )
    c = TestClient(process_api.app)
    # /admin/* is gated on member:manage, which this token does not carry.
    r = c.get("/admin/roles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text
    # The body stays opaque — the caller is told nothing about what they lacked.
    assert r.json() == {"detail": "Forbidden"}

    events = await _events(org["org"], ACCESS_DENIED)
    assert len(events) >= 1, events
    e = events[-1]
    assert e["actor_id"] == "denied-user"
    # ...while the audit row carries exactly what the response withheld.
    assert e["payload"]["permission"] == "member:manage"
    assert "/admin/roles" in e["payload"]["route"]


@pytest.mark.asyncio
async def test_audit_rows_cannot_be_altered_by_the_app_role(org):
    """Append-only is a privilege of the role, not a rule the app could be talked out of.

    The 0001 squash dropped the original REVOKE; it went unnoticed while the app role
    held no grants at all, and reappeared the moment it was granted normal DML.
    """
    await grant_role(
        f"subject-{_uuid.uuid4()}", org["bu"], "developer",
        tenant_id=org["org"], scope_kind="business_unit",
    )

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(Exception) as ei:
            await s.execute(text("UPDATE audit_events SET actor_id = 'tampered'"))
        assert "permission denied" in str(ei.value).lower(), str(ei.value)

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(Exception) as ei:
            await s.execute(text("DELETE FROM audit_events"))
        assert "permission denied" in str(ei.value).lower(), str(ei.value)


@pytest.mark.asyncio
async def test_grant_is_audited_only_when_something_actually_changed(org):
    """The mirror of the revoke rule above, which this side never had.

    `grant_role` is idempotent and re-run constantly — `seed_org_admins` calls it on
    every boot, and under `--reload` that is every code change. It audited
    unconditionally, so 76 of the 105 audit events on the dev database were the same
    no-op appointment of the same org admin, burying the six rows that recorded
    something real.

    `rowcount` cannot tell the difference (the upsert is DO UPDATE, so it is 1 either
    way); `RETURNING (xmax = 0)` can.
    """
    subject = f"subject-{_uuid.uuid4()}"

    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit"
    )
    assert len(await _events(org["org"], RBAC_ROLE_GRANTED)) == 1

    # Same binding, twice more. Nothing changed, so nothing is recorded.
    for _ in range(2):
        await grant_role(
            subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit"
        )
    assert len(await _events(org["org"], RBAC_ROLE_GRANTED)) == 1


@pytest.mark.asyncio
async def test_extending_an_expiry_is_still_audited(org):
    """An extension IS a change. The de-duplication above must not swallow it —
    that is the whole reason the upsert is DO UPDATE rather than DO NOTHING.
    """
    from datetime import datetime, timedelta, timezone

    subject = f"subject-{_uuid.uuid4()}"
    first = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit",
        expires_at=first, granted_by="admin-1",
    )
    assert len(await _events(org["org"], RBAC_ROLE_GRANTED)) == 1

    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit",
        expires_at=first + timedelta(hours=4), granted_by="admin-1",
    )
    assert len(await _events(org["org"], RBAC_ROLE_GRANTED)) == 2


# ── PRD §34.9's before/after ──────────────────────────────────────────────────
#
# A grant recorded only the role it conferred, which answers "what do they hold now"
# and not "what changed" — and those are different questions. `none -> project_admin`
# is an appointment, `developer -> project_admin` is a promotion, and a row reading
# `project_admin` is indistinguishable from either. The Change column on the Audit
# Trail was empty on every row because nothing had ever written the pair.


@pytest.mark.asyncio
async def test_an_appointment_records_that_they_held_nothing_before(org):
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(
        subject, org["bu"], "developer", tenant_id=org["org"],
        scope_kind="business_unit", granted_by="admin-1",
    )

    payload = (await _events(org["org"], RBAC_ROLE_GRANTED))[0]["payload"]
    assert payload["before"] == "none"
    assert payload["after"] == "developer until no expiry"


@pytest.mark.asyncio
async def test_an_extension_records_both_expiries_not_none(org):
    """The distinction the single `role` field could never make.

    An extension and an appointment share an event_type, and read as the same row
    unless the prior state is on it. This is the one that would otherwise look like
    somebody was appointed twice.
    """
    from datetime import datetime, timedelta, timezone

    subject = f"subject-{_uuid.uuid4()}"
    first = datetime.now(tz=timezone.utc) + timedelta(days=1)
    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit",
        expires_at=first, granted_by="admin-1",
    )
    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit",
        expires_at=first + timedelta(days=30), granted_by="admin-1",
    )

    events = await _events(org["org"], RBAC_ROLE_GRANTED)
    assert len(events) == 2, events
    extension = [e for e in events if e["payload"]["before"] != "none"]
    assert len(extension) == 1, [e["payload"] for e in events]
    payload = extension[0]["payload"]
    assert payload["before"] == f"qa until {first.date().isoformat()}"
    assert payload["after"] == f"qa until {(first + timedelta(days=30)).date().isoformat()}"


@pytest.mark.asyncio
async def test_a_revocation_records_what_was_taken_away(org):
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit")
    await revoke_role(
        subject, org["bu"], "qa", tenant_id=org["org"],
        scope_kind="business_unit", revoked_by="admin-1",
    )

    payload = (await _events(org["org"], RBAC_ROLE_REVOKED))[0]["payload"]
    assert payload["before"] == "qa"
    assert payload["after"] == "none"


# ── PRD §34.9: the actor is a real person ────────────────────────────────────
#
# "The real person — never an agent, never a service account, never a role." A NULL
# actor renders as "system", which is honest for the platform seeding its own org
# admins at boot and a LIE for a human action that lost its actor on the way in.
# `getattr(request.state, "user_id", None)` is the shape that loses it, and it appears
# at every grant call site. The two cases must not look alike.


@pytest.mark.asyncio
async def test_a_system_write_says_why_it_had_no_actor(org):
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit",
        system_reason="startup_seeding",
    )

    e = (await _events(org["org"], RBAC_ROLE_GRANTED))[0]
    assert e["actor_id"] is None
    assert e["payload"]["system_reason"] == "startup_seeding"
    assert "actor_unattributed" not in e["payload"]


@pytest.mark.asyncio
async def test_an_unexplained_missing_actor_is_marked_on_the_row(org):
    """The defect case. It must be visible in the trail, not just in a log line.

    A row that cannot say who acted has to SAY that it cannot, rather than reading as
    a system action and being filed with the boot-time seeding.
    """
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(subject, org["bu"], "developer", tenant_id=org["org"], scope_kind="business_unit")

    e = (await _events(org["org"], RBAC_ROLE_GRANTED))[0]
    assert e["actor_id"] is None
    assert e["payload"]["actor_unattributed"] is True
    assert "system_reason" not in e["payload"]


@pytest.mark.asyncio
async def test_a_real_actor_is_never_marked_unattributed(org):
    subject = f"subject-{_uuid.uuid4()}"
    await grant_role(
        subject, org["bu"], "qa", tenant_id=org["org"], scope_kind="business_unit",
        granted_by="a-real-person",
    )

    payload = (await _events(org["org"], RBAC_ROLE_GRANTED))[0]["payload"]
    assert "actor_unattributed" not in payload
    assert "system_reason" not in payload


@pytest.mark.asyncio
async def test_the_boot_seeder_names_itself(org):
    """`seed_org_admins` is the one legitimate unattributed writer on the platform."""
    import inspect

    from shared.auth import bootstrap

    src = inspect.getsource(bootstrap)
    assert 'system_reason="startup_seeding"' in src
