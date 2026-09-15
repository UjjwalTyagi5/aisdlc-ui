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


# ── PRD §34.9's Scope field ──────────────────────────────────────────────────
#
# WHERE a decision landed, which is not the same question as what it landed on: a
# role grant's resource is the person who received it, its scope is the unit they
# received it in. Three writers use three different payload shapes and the scope has
# to be read out of all of them.


@pytest.mark.unit
def test_rbac_events_carry_their_own_scope():
    from shared.routers._schemas import derive_scope

    kind, sid = derive_scope(_Event(
        event_type="rbac.role.granted",
        resource_type="role_binding",
        payload={"scope_kind": "business_unit", "scope_id": "2da60668", "role": "bu_admin"},
    ))
    assert (kind, sid) == ("business_unit", "2da60668")


@pytest.mark.unit
def test_a_project_payload_scopes_to_the_project():
    from shared.routers._schemas import derive_scope

    assert derive_scope(_Event(payload={"project_id": "6aa760d6"})) == ("project", "6aa760d6")


@pytest.mark.unit
def test_a_workspace_payload_scopes_to_the_unit():
    from shared.routers._schemas import derive_scope

    assert derive_scope(_Event(payload={"workspace_id": "2da60668"})) == (
        "business_unit",
        "2da60668",
    )


@pytest.mark.unit
def test_a_business_unit_resource_is_its_own_scope():
    """An access.denied names the unit as its resource and carries nothing else."""
    from shared.routers._schemas import derive_scope

    assert derive_scope(_Event(resource_type="business_unit", resource_id="2da60668")) == (
        "business_unit",
        "2da60668",
    )


@pytest.mark.unit
def test_everything_else_happened_at_the_organization():
    """The honest default: it is the one scope that always exists."""
    from shared.routers._schemas import derive_scope

    assert derive_scope(_Event(tenant_id="t-1", resource_type="thing", payload=None)) == (
        "organization",
        "t-1",
    )


@pytest.mark.unit
def test_the_scope_name_reaches_the_response():
    out = AuditEventOut.from_orm_audit(
        _Event(payload={"scope_kind": "business_unit", "scope_id": "2da60668"}),
        scope_name="Demo",
    )
    assert out.scope.kind == "business_unit"
    assert out.scope.id == "2da60668"
    assert out.scope.name == "Demo"


# ── Write-time capture: the record must outlive what it points at ────────────


@pytest.mark.unit
def test_a_captured_scope_name_survives_the_thing_being_deleted():
    """THE WHOLE POINT OF CAPTURING IT.

    Read-time resolution joins the trail to mutable tables. `seed_dev_personas`
    created "Core ledger — Java 8 to 21", granted seven roles in it, and the project
    was later dropped — so those grants render as `Project fa5e4ce1…` and no join can
    ever recover the name. An event that recorded the name at write time is legible
    whether or not the project still exists, which is what PRD §34.9's "legible
    without guessing" requires.

    Modelled here as the resolver returning nothing, which is exactly what a deleted
    row looks like from `_resolve_names`.
    """
    out = AuditEventOut.from_orm_audit(
        _Event(payload={
            "scope_kind": "project",
            "scope_id": "fa5e4ce1-738f-49ea-99be-1bbbdd1363d5",
            "scope_name": "Core ledger — Java 8 to 21",
        }),
        scope_name=None,
    )
    assert out.scope.name == "Core ledger — Java 8 to 21"


@pytest.mark.unit
def test_the_captured_scope_name_wins_over_a_later_rename():
    """A unit renamed afterwards must not retitle the events under its old name."""
    out = AuditEventOut.from_orm_audit(
        _Event(payload={"scope_kind": "business_unit", "scope_id": "2da60668",
                        "scope_name": "Demo (as it was called then)"}),
        scope_name="Renamed Later",
    )
    assert out.scope.name == "Demo (as it was called then)"


@pytest.mark.unit
def test_captured_names_are_not_repeated_in_the_detail_blob():
    """They have their own columns; the detail panel is for what is NOT on screen."""
    out = AuditEventOut.from_orm_audit(
        _Event(payload={
            "scope_kind": "project", "scope_id": "p1", "scope_name": "Dummy T1",
            "actor_name": "a@b.com", "resource_name": "c@d.com", "role": "developer",
        }),
    )
    assert out.detail == {"scope_kind": "project", "scope_id": "p1", "role": "developer"}


@pytest.mark.unit
def test_the_writer_and_the_reader_look_in_the_same_tables():
    """Two mappings that disagree would capture one name and resolve a different one."""
    from shared.authz.audit import _NAME_SOURCES
    from shared.routers.audit import _SCOPE_NAME_SOURCES

    for kind, source in _NAME_SOURCES.items():
        assert _SCOPE_NAME_SOURCES[kind] == source, kind


# ── Every writer, not just the RBAC one ──────────────────────────────────────
#
# Artifacts, runs, deployments and artifact versions each build their own AuditEvent
# and each stored ids alone, so each had the same failure: delete the project and the
# record of what was approved inside it stops naming it, permanently. `capture_names`
# is the single entry point they now share.


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capture_names_reads_whichever_shape_the_writer_used():
    """Four writers, four payload shapes, one answer for "what is this about"."""
    from shared.authz.audit import capture_names

    class _Session:
        """Names a project `P`, a workspace `W`, and one user."""

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            ident = (params or {}).get("i", "")

            class _R:
                name = "P" if "projects" in sql else "W"
                email = "a@b.com"

            class _Res:
                def first(_self):
                    if "users" in sql:
                        return _R() if ident == "u1" else None
                    return _R() if ident in ("p1", "w1") else None

            return _Res()

    s = _Session()
    assert (await capture_names(s, {"project_id": "p1"}, actor_id="u1"))["scope_name"] == "P"
    assert (await capture_names(s, {"workspace_id": "w1"}))["scope_name"] == "W"
    rbac = await capture_names(s, {"scope_kind": "business_unit", "scope_id": "w1"})
    assert rbac["scope_name"] == "W"
    assert (await capture_names(s, {"project_id": "p1"}, actor_id="u1"))["actor_name"] == "a@b.com"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capture_names_never_overwrites_the_caller():
    """A name the writer set deliberately is the one that was true; it wins."""
    from shared.authz.audit import capture_names

    class _S:
        async def execute(self, *a, **k):
            class _Res:
                def first(_self):
                    class _R:
                        name = "Resolved"
                        email = "resolved@b.com"
                    return _R()
            return _Res()

    out = await capture_names(
        _S(), {"project_id": "p1", "scope_name": "Kept", "actor_name": "kept@b.com"},
        actor_id="u1",
    )
    assert out["scope_name"] == "Kept"
    assert out["actor_name"] == "kept@b.com"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_lookup_leaves_the_record_alone():
    """An audit write must never fail because a name could not be read."""
    from shared.authz.audit import capture_names

    class _Broken:
        async def execute(self, *a, **k):
            raise RuntimeError("database is having a day")

    out = await capture_names(_Broken(), {"project_id": "p1"}, actor_id="u1")
    assert out == {"project_id": "p1"}


@pytest.mark.unit
def test_every_audit_writer_goes_through_capture_names():
    """The sweep, pinned. A new writer that stores bare ids reintroduces the bug.

    Checked as source text rather than behaviour because the alternative is a live
    write per call site into an APPEND-ONLY table — there is no cleanup afterwards.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    writers = [
        "shared/routers/artifacts.py",
        "shared/routers/runs.py",
        "shared/services/deployment_gate.py",
        "shared/services/artifact_versions.py",
        "shared/audit/service.py",
    ]
    for rel in writers:
        src = (backend / rel).read_text(encoding="utf-8")
        assert "capture_names" in src, f"{rel} builds an AuditEvent without naming it"
        # Every AuditEvent( construction in these files must take a captured payload.
        assert src.count("payload=await capture_names(") >= src.count("AuditEvent("), rel


# ── PRD §34.9: "Export is itself an audited event" ───────────────────────────


@pytest.mark.unit
def test_the_export_action_has_a_name_of_its_own():
    """Taking the trail is an act ON the trail, and needs its own vocabulary entry.

    Filed with the other constants rather than written as a literal at the call site
    for the reason that module already gives: these strings are queried by the UI and
    by compliance exports, and a typo produces a category nothing reads.
    """
    from shared.authz.audit import AUDIT_EXPORTED

    assert AUDIT_EXPORTED == "audit.exported"


@pytest.mark.unit
def test_the_export_is_bounded():
    """An export is a file somebody opens, not a replication channel."""
    from shared.routers.audit import _EXPORT_MAX

    assert 0 < _EXPORT_MAX <= 50_000


@pytest.mark.unit
def test_the_export_and_the_screen_share_one_query():
    """An export that filtered differently from the page it was taken from would be
    the worst kind of wrong: a file that looks like what you were reading and is not.
    """
    import inspect

    from shared.routers import audit as mod

    src = inspect.getsource(mod.export_audit_events)
    assert "_query_audit_events" in src
    assert "record_rbac_change" in src, "an export that is not recorded is not audited"

    list_src = inspect.getsource(mod.list_audit_events)
    assert "_query_audit_events" in list_src
