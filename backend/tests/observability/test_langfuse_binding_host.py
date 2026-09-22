"""A Langfuse binding belongs to the instance that minted its keys.

Switching LANGFUSE_HOST from one Langfuse to another — the retired remote instance for a
self-hosted one — left every project's binding pointing at the old instance: its keys exist
only in the old database, and `client_for_binding` sends traces to the binding's own host.
Tracing fails open, so a project could look configured while every trace went to a server
that no longer answers. These tests pin the rule that fixes it, against the real table:

    a binding for another instance is not a binding for this one — it is not loaded, not
    revived, and it is retired (kept, deactivated) when this instance provisions its own.
"""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.observability import bindings as _bindings

pytestmark = pytest.mark.usefixtures("purge_created_orgs")

OLD = "https://old-langfuse.example"
NEW = "http://127.0.0.1:3100"


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
def configured_host(monkeypatch):
    """Point this process at the NEW instance, the way LANGFUSE_HOST would."""
    import config.env as env

    monkeypatch.setattr(env, "LANGFUSE_HOST", NEW)
    return NEW


@pytest.fixture
async def project():
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Binding Host')"
        ), {"i": org, "s": f"bh-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Traced')"
        ), {"i": proj, "w": bu, "t": org})

    yield {"org": org, "bu": bu, "proj": proj}

    async with get_db_session_for_tenant(org) as s:
        for table in ("langfuse_bindings", "projects"):
            await s.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = CAST(:t AS uuid)"), {"t": org}
            )
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text(
            "DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


async def _bind(p: dict, *, host: str, active: bool, lf_project: str) -> None:
    async with get_db_session_for_tenant(p["org"]) as s:
        await s.execute(text(
            "insert into langfuse_bindings (id, tenant_id, workspace_id, project_id, "
            " langfuse_org_id, langfuse_project_id, langfuse_project_name, langfuse_host, "
            " public_key_encrypted, secret_key_encrypted, is_active) "
            "values (gen_random_uuid(), :t, :w, :p, 'lf-org', :lp, :lp, :h, :pk, :sk, :a)"
        ), {
            "t": p["org"], "w": p["bu"], "p": p["proj"], "lp": lf_project, "h": host,
            "pk": _bindings._encrypt(f"pk-{lf_project}"),
            "sk": _bindings._encrypt(f"sk-{lf_project}"), "a": active,
        })


async def _rows(p: dict) -> dict[str, bool]:
    """langfuse_project_id -> is_active, for this project."""
    async with get_db_session_for_tenant(p["org"]) as s:
        rows = (await s.execute(text(
            "select langfuse_project_id, is_active from langfuse_bindings "
            "where project_id = cast(:p as uuid)"
        ), {"p": p["proj"]})).all()
    return {r[0]: r[1] for r in rows}


def _provision_refuses(monkeypatch):
    """Provisioning unreachable: ensure_binding must then answer from the table alone."""
    from shared.observability import provisioning

    async def _refuse(self, **kwargs):
        raise provisioning.LangfuseProvisioningError("no Langfuse database in this test")

    monkeypatch.setattr(provisioning.LangfuseProvisioner, "provision", _refuse)


async def test_a_binding_for_another_instance_is_not_loaded(project, configured_host):
    await _bind(project, host=OLD, active=True, lf_project="old-proj")
    async with get_db_session_for_tenant(project["org"]) as s:
        assert await _bindings.load_binding(s, project["org"], project["proj"]) is None


async def test_a_binding_for_this_instance_is_loaded_trailing_slash_or_not(project, configured_host):
    await _bind(project, host=NEW + "/", active=True, lf_project="new-proj")
    async with get_db_session_for_tenant(project["org"]) as s:
        b = await _bindings.load_binding(s, project["org"], project["proj"])
    assert b is not None and b.langfuse_project_id == "new-proj"
    assert b.public_key == "pk-new-proj"


async def test_only_this_instances_archived_binding_is_revived(project, configured_host, monkeypatch):
    """The OLD instance's row is the most recently touched — the query used to take it."""
    _provision_refuses(monkeypatch)
    await _bind(project, host=NEW, active=False, lf_project="new-proj")
    await _bind(project, host=OLD, active=False, lf_project="old-proj")
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "update langfuse_bindings set updated_at = now() + interval '1 hour' "
            "where langfuse_project_id = 'old-proj'"
        ))

    async with get_db_session_for_tenant(project["org"]) as s:
        b = await _bindings.ensure_binding(s, tenant_id=project["org"], project_id=project["proj"])

    assert b is not None and b.langfuse_project_id == "new-proj"
    assert await _rows(project) == {"new-proj": True, "old-proj": False}


async def test_another_instances_archived_binding_is_never_revived(project, configured_host, monkeypatch):
    _provision_refuses(monkeypatch)
    await _bind(project, host=OLD, active=False, lf_project="old-proj")

    async with get_db_session_for_tenant(project["org"]) as s:
        assert await _bindings.ensure_binding(
            s, tenant_id=project["org"], project_id=project["proj"]) is None
    assert await _rows(project) == {"old-proj": False}


async def test_provisioning_here_retires_the_other_instances_active_binding(
    project, configured_host, monkeypatch
):
    """One active binding per project (partial unique index). Without retiring the old
    instance's, the new insert was a silent `on conflict do nothing` and the project
    went on tracing to the old host."""
    from shared.observability import provisioning

    async def _provisioned(self, **kwargs):
        return provisioning.ProvisionedProject(
            langfuse_org_id="new-org", langfuse_project_id="new-proj",
            langfuse_project_name="Traced", public_key="pk-new-proj",
            secret_key="sk-new-proj", host=NEW, created_org=False,
            created_project=True,
        )

    monkeypatch.setattr(provisioning.LangfuseProvisioner, "provision", _provisioned)
    await _bind(project, host=OLD, active=True, lf_project="old-proj")

    async with get_db_session_for_tenant(project["org"]) as s:
        b = await _bindings.ensure_binding(s, tenant_id=project["org"], project_id=project["proj"])

    assert b is not None and (b.langfuse_project_id, b.langfuse_host) == ("new-proj", NEW)
    # Kept, not deleted: pointing LANGFUSE_HOST back revives it with its history.
    assert await _rows(project) == {"old-proj": False, "new-proj": True}


async def test_a_replacement_organization_is_recorded_so_the_units_next_project_reuses_it(
    project, configured_host, monkeypatch
):
    """Observed live: the unit named an organization the new instance did not have.
    QuickLink got a replacement 'PAYMENTS'; the unit's next project, still told the old
    id, got ANOTHER one, 'PAYMENTS (PWC)'. Recording the replacement is what stops that."""
    from shared.observability import provisioning

    asked_for: list = []

    async def _provisioned(self, *, org_id=None, project_name="", **kwargs):
        asked_for.append(org_id)
        return provisioning.ProvisionedProject(
            langfuse_org_id="replacement-org", langfuse_project_id=f"lf-{project_name}",
            langfuse_project_name=project_name, public_key=f"pk-{project_name}",
            secret_key=f"sk-{project_name}", host=NEW, created_org=org_id != "replacement-org",
            created_project=True, langfuse_org_name="Unit",
        )

    monkeypatch.setattr(provisioning.LangfuseProvisioner, "provision", _provisioned)
    second = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "update workspaces set langfuse_org_id = 'gone-org' where id = cast(:w as uuid)"
        ), {"w": project["bu"]})
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Second')"
        ), {"i": second, "w": project["bu"], "t": project["org"]})

    for pid in (project["proj"], second):
        async with get_db_session_for_tenant(project["org"]) as s:
            assert await _bindings.ensure_binding(s, tenant_id=project["org"], project_id=pid)

    # The first project was told the vanished id; the second, the replacement.
    assert asked_for == ["gone-org", "replacement-org"]
    async with get_db_session_superuser() as s:
        row = (await s.execute(text(
            "select langfuse_org_id, langfuse_org_name from workspaces where id = cast(:w as uuid)"
        ), {"w": project["bu"]})).first()
    assert tuple(row) == ("replacement-org", "Unit")
