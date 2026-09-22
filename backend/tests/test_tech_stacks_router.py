"""Who may do what with tech stacks, and what the API answers — against an in-memory store."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers import tech_stacks as r  # noqa: E402
from shared.services.tech_stack import TechStack, decide_effective  # noqa: E402

T, W1, W2, P1 = "t-1", "w-1", "w-2", "p-1"


class FakeStore:
    DuplicateStackName = type("DuplicateStackName", (ValueError,), {})

    def __init__(self):
        self.stacks: dict[str, TechStack] = {}
        self.selection: dict[str, str] = {}
        self.n = 0
        self.invalidated = False

    async def project_scope(self, tenant, project):
        return (project == P1), (W1 if project == P1 else None)

    async def workspace_exists(self, tenant, ws):
        return ws in (W1, W2)

    async def list_workspace_stacks(self, tenant, ws):
        return [s for s in self.stacks.values() if s.workspace_id == ws and not s.deleted]

    async def list_project_stacks(self, tenant, project):
        return [s for s in self.stacks.values() if s.project_id == project and not s.deleted]

    async def get_stack(self, tenant, sid, include_deleted=False):
        s = self.stacks.get(sid)
        return s if s and (include_deleted or not s.deleted) else None

    async def create_stack(self, tenant, *, scope, scope_id, fields, actor):
        if any(s.name.lower() == fields["name"].lower() and (s.workspace_id or s.project_id) == scope_id
               and not s.deleted for s in self.stacks.values()):
            raise self.DuplicateStackName(fields["name"])
        self.n += 1
        s = TechStack(id=f"s{self.n}", scope=scope, workspace_id=scope_id if scope == "workspace" else None,
                      project_id=scope_id if scope == "project" else None, **fields)
        self.stacks[s.id] = s
        return s

    async def update_stack(self, tenant, sid, fields, actor):
        self.stacks[sid] = replace(self.stacks[sid], **fields)
        return self.stacks[sid]

    async def delete_stack(self, tenant, sid, actor):
        self.stacks[sid] = replace(self.stacks[sid], deleted=True, is_default=False)
        return self.stacks[sid]

    async def set_default(self, tenant, sid, is_default, actor):
        s = self.stacks[sid]
        for k, v in list(self.stacks.items()):
            if v.workspace_id == s.workspace_id and v.is_default:
                self.stacks[k] = replace(v, is_default=False)
        self.stacks[sid] = replace(self.stacks[sid], is_default=is_default)
        return self.stacks[sid]

    async def get_selection(self, tenant, project):
        return self.selection.get(project)

    async def set_selection(self, tenant, project, sid, actor):
        if sid is None:
            self.selection.pop(project, None)
        else:
            self.selection[project] = sid

    async def resolve_project_tech_stack(self, tenant, project):
        sel = self.selection.get(project)
        default = next((s for s in self.stacks.values()
                        if s.workspace_id == W1 and s.is_default and not s.deleted), None)
        return decide_effective(project_id=project, workspace_id=W1,
                                selected=self.stacks.get(sel) if sel else None,
                                selection_id=sel, default=default)

    def invalidate_tech_stack_cache(self, tenant=None):
        self.invalidated = True


@pytest.fixture
def env(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(r, "store", fake)
    owners: set[tuple[str, str]] = set()

    async def tier_access(tenant, user, perms, scope, scope_id):
        return ((scope, scope_id) in owners), False

    monkeypatch.setattr(r, "resolve_actor_tier_access", tier_access)
    monkeypatch.setattr(r, "_assert_project_visible", AsyncMock())
    monkeypatch.setattr(r, "_emit", AsyncMock())
    return SimpleNamespace(store=fake, owners=owners)


def req(user="u-1"):
    return SimpleNamespace(state=SimpleNamespace(tenant_id=T, user_id=user, permissions=[]))


def body(**kw):
    base = {"name": "Node + Next.js", "description": "", "categories": {"languages": ["TypeScript"]}, "notes": ""}
    base.update(kw)
    return base


def create_in(scope, scope_id, **kw):
    return r.CreateTechStackIn(scope=scope, scope_id=scope_id, **body(**kw))


async def test_a_bu_admin_creates_a_stack_and_a_non_owner_cannot(env):
    env.owners.add(("workspace", W1))
    out = await r.create_tech_stack(create_in("workspace", W1), req())
    assert out["name"] == "Node + Next.js" and out["workspace_id"] == W1 and out["is_default"] is False
    assert env.store.invalidated is True
    with pytest.raises(HTTPException) as denied:
        await r.create_tech_stack(create_in("workspace", W2), req())
    assert denied.value.status_code == 403 and "Business Unit admin" in denied.value.detail


async def test_invalid_and_duplicate_stacks_answer_violations(env):
    env.owners.add(("workspace", W1))
    with pytest.raises(HTTPException) as bad:
        await r.create_tech_stack(create_in("workspace", W1, name="x", categories={}), req())
    assert bad.value.status_code == 422
    assert {v["code"] for v in bad.value.detail["violations"]} == {"name_length", "empty_stack"}
    await r.create_tech_stack(create_in("workspace", W1), req())
    with pytest.raises(HTTPException) as dup:
        await r.create_tech_stack(create_in("workspace", W1, name="node + next.js"), req())
    assert dup.value.detail["violations"][0]["code"] == "duplicate_name"


async def test_an_unknown_scope_or_missing_business_unit_is_refused(env):
    env.owners.add(("workspace", "w-nope"))
    with pytest.raises(HTTPException) as scope:
        await r.create_tech_stack(create_in("org", "x"), req())
    assert scope.value.status_code == 422
    with pytest.raises(HTTPException) as missing:
        await r.create_tech_stack(create_in("workspace", "w-nope"), req())
    assert missing.value.status_code == 404


async def test_one_default_per_business_unit_and_only_a_bu_stack_can_be_it(env):
    env.owners |= {("workspace", W1), ("project", P1)}
    a = await r.create_tech_stack(create_in("workspace", W1, name="Java"), req())
    b = await r.create_tech_stack(create_in("workspace", W1, name="Node"), req())
    await r.set_tech_stack_default(a["id"], r.DefaultIn(is_default=True), req())
    await r.set_tech_stack_default(b["id"], r.DefaultIn(is_default=True), req())
    listed = await r.list_business_unit_tech_stacks(req(), workspace_id=W1)
    assert [s["name"] for s in listed["items"] if s["is_default"]] == ["Node"] and listed["can_manage"] is True
    own = await r.create_tech_stack(create_in("project", P1, name="Own"), req())
    with pytest.raises(HTTPException) as not_bu:
        await r.set_tech_stack_default(own["id"], r.DefaultIn(is_default=True), req())
    assert not_bu.value.status_code == 422


async def test_only_the_owner_edits_or_deletes(env):
    env.owners.add(("workspace", W1))
    s = await r.create_tech_stack(create_in("workspace", W1, name="Java"), req())
    env.owners.clear()
    with pytest.raises(HTTPException) as edit:
        await r.update_tech_stack(s["id"], r.TechStackFields(**body(name="Java 21")), req())
    assert edit.value.status_code == 403
    with pytest.raises(HTTPException) as gone:
        await r.delete_tech_stack("s-missing", req())
    assert gone.value.status_code == 404
    env.owners.add(("workspace", W1))
    edited = await r.update_tech_stack(s["id"], r.TechStackFields(**body(name="Java 21")), req())
    assert edited["name"] == "Java 21"


async def test_a_project_admin_picks_one_and_the_view_shows_what_agents_follow(env):
    env.owners |= {("workspace", W1), ("project", P1)}
    java = await r.create_tech_stack(create_in("workspace", W1, name="Java"), req())
    node = await r.create_tech_stack(create_in("workspace", W1, name="Node"), req())
    await r.set_tech_stack_default(node["id"], r.DefaultIn(is_default=True), req())
    view = await r.get_project_tech_stack(P1, req())
    assert view["effective"]["source"] == "bu_default" and view["effective"]["stack"]["name"] == "Node"
    assert view["selection"]["tech_stack_id"] is None and view["can_manage"] is True
    assert [s["name"] for s in view["options"]["business_unit"]] == ["Java", "Node"]
    view = await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=java["id"]), req())
    assert view["effective"]["stack"]["name"] == "Java" and view["effective"]["source"] == "project_selection"
    view = await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=None), req())
    assert view["effective"]["stack"]["name"] == "Node"


async def test_a_project_cannot_pick_another_business_units_stack_and_only_its_admin_picks(env):
    env.owners |= {("workspace", W2)}
    other = await r.create_tech_stack(create_in("workspace", W2, name="Other"), req())
    with pytest.raises(HTTPException) as denied:
        await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=other["id"]), req())
    assert denied.value.status_code == 403
    env.owners.add(("project", P1))
    with pytest.raises(HTTPException) as foreign:
        await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=other["id"]), req())
    assert foreign.value.status_code == 409


async def test_deleting_the_selected_stack_falls_back_with_a_warning(env):
    env.owners |= {("workspace", W1), ("project", P1)}
    java = await r.create_tech_stack(create_in("workspace", W1, name="Java"), req())
    await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=java["id"]), req())
    assert (await r.delete_tech_stack(java["id"], req())) == {"deleted": True, "id": java["id"]}
    view = await r.get_project_tech_stack(P1, req())
    assert view["effective"]["stack"] is None and "was deleted" in view["effective"]["warning"]


async def test_an_unknown_project_is_not_found(env):
    with pytest.raises(HTTPException) as missing:
        await r.get_project_tech_stack("p-nope", req())
    assert missing.value.status_code == 404


def test_every_route_sits_behind_the_view_floor():
    assert r.tech_stacks_router.dependencies, "the router carries the artifact:view floor"
    paths = {route.path for route in r.tech_stacks_router.routes}
    assert {"/tech-stacks", "/tech-stacks/catalog", "/tech-stacks/{stack_id}", "/tech-stacks/{stack_id}/default",
            "/projects/{project_id}/tech-stack"} <= paths
