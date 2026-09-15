"""The Audit Trail must say who and what, not print two UUIDs.

`actor_name` and `resource_name` are payload keys that NOTHING writes — not one of
the 132 events on the dev database carries either — so `AuditEventOut` fell through
to the raw id for both, every time. The page rendered as columns of UUID: you could
see that somebody had been denied `role:manage` on a business unit, and not who or
which one.

Its own file rather than test_audit_api.py, for the reason recorded in
test_audit_actor_filter.py: that module carries a stale module-level
`xfail(strict=False)`, so anything added there gates nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.routers._schemas import AuditEventOut


class _Event:
    """The handful of ORM attributes `from_orm_audit` reads."""

    def __init__(self, **kw):
        self.id = kw.get("id", "e1")
        self.tenant_id = kw.get("tenant_id", "t1")
        self.actor_id = kw.get("actor_id")
        self.event_type = kw.get("event_type", "access.denied")
        self.resource_type = kw.get("resource_type", "business_unit")
        self.resource_id = kw.get("resource_id", "2da60668-6c5c-4909-84c2-9b99f3c79f4c")
        self.payload = kw.get("payload")
        self.created_at = kw.get("created_at", datetime(2026, 9, 15, tzinfo=timezone.utc))


@pytest.mark.unit
def test_resolved_names_reach_the_response():
    """The live failure: an access.denied on a unit, with both ends unreadable."""
    out = AuditEventOut.from_orm_audit(
        _Event(actor_id="bc0a85e2-f512-40a4-ab72-2140edd12cc6"),
        actor_name="akshatsehgal05@gmail.com",
        resource_name="Demo",
    )
    assert out.actor.name == "akshatsehgal05@gmail.com"
    assert out.resource.name == "Demo"


@pytest.mark.unit
def test_the_id_survives_alongside_the_name():
    """Naming a thing must not make it unsearchable — the row still carries the id."""
    out = AuditEventOut.from_orm_audit(
        _Event(actor_id="bc0a85e2-f512-40a4-ab72-2140edd12cc6"),
        actor_name="akshatsehgal05@gmail.com",
        resource_name="Demo",
    )
    assert out.actor.id == "bc0a85e2-f512-40a4-ab72-2140edd12cc6"
    assert out.resource.id == "2da60668-6c5c-4909-84c2-9b99f3c79f4c"


@pytest.mark.unit
def test_the_payload_name_wins_over_the_resolved_one():
    """What was true AT THE TIME is the stronger claim for a log.

    A unit renamed after the event happened must not retitle the event.
    """
    out = AuditEventOut.from_orm_audit(
        _Event(payload={"resource_name": "Demo (as it was called then)"}),
        resource_name="Renamed Later",
    )
    assert out.resource.name == "Demo (as it was called then)"


@pytest.mark.unit
def test_an_unresolvable_resource_keeps_its_id_and_no_name():
    """Twelve seeded rows point at slugs like `demo-dev`, which no table can name."""
    out = AuditEventOut.from_orm_audit(_Event(resource_type="role_binding", resource_id="demo-dev"))
    assert out.resource.name is None
    assert out.resource.id == "demo-dev"


@pytest.mark.unit
def test_an_unresolvable_actor_still_falls_back_to_the_id():
    """An id is worse than a name and much better than an empty cell."""
    out = AuditEventOut.from_orm_audit(_Event(actor_id="who-is-this"))
    assert out.actor.name == "who-is-this"


@pytest.mark.unit
def test_a_null_actor_is_still_the_system():
    out = AuditEventOut.from_orm_audit(_Event(actor_id=None))
    assert out.actor.id == "system"
    assert out.actor.name == "system"


@pytest.mark.unit
def test_an_upload_names_itself_from_its_own_payload():
    """No table lookup beats the name the file was given."""
    out = AuditEventOut.from_orm_audit(
        _Event(
            event_type="artifact_upload",
            resource_type="artifact",
            resource_id="31d7beff-76f6-441f-b191-9d493025ecd3",
            payload={"filename": "QuickLink_Discovery_Call_Transcript.docx"},
        )
    )
    assert out.resource.name == "QuickLink_Discovery_Call_Transcript.docx"


@pytest.mark.unit
def test_the_project_is_named_too():
    out = AuditEventOut.from_orm_audit(
        _Event(payload={"project_id": "6aa760d6-f3be-47a2-8af6-891218243751"}),
        project_name="Dummy T1",
    )
    assert out.projectId == "6aa760d6-f3be-47a2-8af6-891218243751"
    assert out.projectName == "Dummy T1"


@pytest.mark.unit
def test_rbac_events_are_named_from_users_not_role_bindings():
    """shared/authz/audit.py records the SUBJECT of a grant as the resource.

    Of the 87 rbac events on the dev database, 75 join to `users` and NONE join to
    `role_bindings` — so looking them up in the table their type is named after
    would name none of them. This is the mapping that decides it.
    """
    from shared.routers.audit import _RESOURCE_NAME_SOURCES

    assert _RESOURCE_NAME_SOURCES["role_binding"][0] == "users"
    assert _RESOURCE_NAME_SOURCES["business_unit"] == ("workspaces", "display_name")


@pytest.mark.unit
def test_no_resolution_at_all_is_the_old_behaviour():
    """The run-scoped trail and the older tests call this with no names at all."""
    out = AuditEventOut.from_orm_audit(_Event(actor_id="u1"))
    assert out.actor.name == "u1"
    assert out.resource.name is None
    assert out.projectName is None
