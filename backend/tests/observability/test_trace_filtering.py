"""GET /traces filtering — pushed into Langfuse, and never able to widen the caller's scope.

Two things are being pinned here.

CORRECT PAGES. Filtering after the page comes back returns however many of those rows
happened to match — for a scoped caller, frequently fewer than `limit` and sometimes
zero, with no way to ask for the rest. So project goes down as a `project:` tag, user
goes down as Langfuse's first-class `userId`, and a scoped caller with several projects
gets one query per project merged newest-first rather than one tenant-wide page trimmed.

ISOLATION. The tenant tag is the whole boundary between tenants here — Langfuse is one
shared project, so unlike cost/audit there is no RLS underneath. It must be derived from
the token on every path, and naming a project you cannot see must not make it readable.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _req(tenant_id="t1", permissions=("admin:*",), user_id="u"):
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_id=tenant_id, permissions=list(permissions), user_id=user_id
        )
    )


def _trace(tid, project=None, ts="2026-09-01T00:00:00Z"):
    meta = {"agent_type": "design"}
    if project:
        meta["project_id"] = project
    return {
        "id": tid, "name": "sdlc:design", "metadata": meta, "timestamp": ts,
        "latency": 1.0, "totalCost": 0.01, "observations": [], "environment": "default",
    }


def _capture(monkeypatch, tr, payloads=None):
    """Record every Langfuse call; answer from `payloads` keyed by the project tag.

    These tests cover the PRE-BINDING fallback — one shared Langfuse project with tag
    filtering — which is still the path for a tenant that has no bindings yet. They pass
    `db=None`, so `_readable_bindings` fails its lookup and returns None, selecting that
    fallback. It also needs the shared key pair to be configured, which the test
    environment does not set, so that is declared here rather than left to `.env.test`.
    """
    monkeypatch.setattr(tr, "_shared_project_configured", lambda: True)
    calls: list[dict] = []

    async def _fake_get(path, params):
        calls.append(dict(params))
        tags = params.get("tags") or []
        tags = [tags] if isinstance(tags, str) else list(tags)
        proj = next((t.split(":", 1)[1] for t in tags if t.startswith("project:")), None)
        return {"data": (payloads or {}).get(proj, [])}

    monkeypatch.setattr(tr, "_lf_get", _fake_get)
    return calls


def test_project_filter_is_pushed_down_as_a_tag(monkeypatch):
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)
    calls = _capture(monkeypatch, tr, {"p1": [_trace("t-1", "p1")]})

    rows = asyncio.run(tr.list_traces(_req(), project="p1", limit=50, db=None))

    assert len(calls) == 1
    tags = calls[0]["tags"]
    assert "tenant:t1" in tags and "project:p1" in tags
    assert [r.id for r in rows] == ["t-1"]


def test_user_filter_is_pushed_down_as_langfuse_user_id(monkeypatch):
    """userId is a first-class query field, so paging stays correct for one person's runs."""
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)
    calls = _capture(monkeypatch, tr)

    asyncio.run(tr.list_traces(_req(), user="alice", limit=50, db=None))

    assert calls and calls[0].get("userId") == "alice"


def test_org_wide_caller_makes_one_untagged_query(monkeypatch):
    """No fan-out when the caller can see everything — one tenant-tagged query."""
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)
    calls = _capture(monkeypatch, tr)

    asyncio.run(tr.list_traces(_req(permissions=("admin:*",)), limit=50, db=None))

    assert len(calls) == 1
    assert calls[0]["tags"] == "tenant:t1"


def test_scoped_caller_fans_out_one_query_per_visible_project(monkeypatch):
    """The short-page fix: N projects -> N tagged queries, merged newest-first.

    Previously this fetched ONE tenant-wide page and dropped the rows the caller could
    not see, so a project admin whose traces were not in the newest N tenant-wide rows
    got a short page — or an empty one — and no way to reach the rest.
    """
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)

    async def _visible(db, request):
        return {"p1", "p2"}

    monkeypatch.setattr(tr, "_visible_projects", _visible)
    calls = _capture(monkeypatch, tr, {
        "p1": [_trace("older", "p1", "2026-09-01T00:00:00Z")],
        "p2": [_trace("newer", "p2", "2026-09-02T00:00:00Z")],
    })

    rows = asyncio.run(tr.list_traces(_req(permissions=("trace:view",)), limit=50, db=None))

    assert len(calls) == 2
    projects = sorted(
        t.split(":", 1)[1]
        for c in calls for t in c["tags"] if t.startswith("project:")
    )
    assert projects == ["p1", "p2"]
    # Merged on timestamp, newest first — the order Langfuse would have returned.
    assert [r.id for r in rows] == ["newer", "older"]


def test_caller_who_sees_no_projects_queries_nothing(monkeypatch):
    """An empty visible set means no rows — and no reason to call Langfuse at all."""
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)

    async def _visible(db, request):
        return set()

    monkeypatch.setattr(tr, "_visible_projects", _visible)
    calls = _capture(monkeypatch, tr)

    rows = asyncio.run(tr.list_traces(_req(permissions=("trace:view",)), limit=50, db=None))

    assert rows == []
    assert calls == []


def test_naming_a_foreign_project_does_not_make_it_readable(monkeypatch):
    """The pushed-down tag is a narrowing, not an authorization.

    Scoping still runs on the returned rows, so asking for a project outside the
    caller's scope yields nothing rather than that project's traces.
    """
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)

    async def _visible(db, request):
        return {"p1"}

    monkeypatch.setattr(tr, "_visible_projects", _visible)
    _capture(monkeypatch, tr, {"p-foreign": [_trace("secret", "p-foreign")]})

    rows = asyncio.run(
        tr.list_traces(_req(permissions=("trace:view",)), project="p-foreign", db=None)
    )

    assert rows == []


def test_tenant_tag_comes_from_the_token_not_the_request(monkeypatch):
    """Every outbound query carries the CALLER'S tenant, whatever else is asked for.

    This is the whole cross-tenant boundary for traces: Langfuse is one shared project,
    so there is no RLS underneath to catch a mistake here.
    """
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)
    calls = _capture(monkeypatch, tr)

    asyncio.run(
        tr.list_traces(
            _req(tenant_id="t-mine"),
            project="tenant:t-other",  # a tag-shaped value smuggled through a filter
            user="alice",
            limit=50,
            db=None,
        )
    )

    for c in calls:
        tags = c["tags"]
        tags = [tags] if isinstance(tags, str) else list(tags)
        assert "tenant:t-mine" in tags
        assert not any(t == "tenant:t-other" for t in tags)


@pytest.mark.parametrize("n", [1, 8])
def test_fanout_stays_within_its_cap(monkeypatch, n):
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)
    projects = {f"p{i}" for i in range(n)}

    async def _visible(db, request):
        return projects

    monkeypatch.setattr(tr, "_visible_projects", _visible)
    calls = _capture(monkeypatch, tr)

    asyncio.run(tr.list_traces(_req(permissions=("trace:view",)), limit=50, db=None))

    assert len(calls) == n <= tr._MAX_PROJECT_FANOUT


def test_beyond_the_cap_falls_back_to_a_single_query(monkeypatch):
    """Past the cap, one page filtered afterwards beats N round-trips per page load."""
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)

    async def _visible(db, request):
        return {f"p{i}" for i in range(tr._MAX_PROJECT_FANOUT + 1)}

    monkeypatch.setattr(tr, "_visible_projects", _visible)
    calls = _capture(monkeypatch, tr)

    asyncio.run(tr.list_traces(_req(permissions=("trace:view",)), limit=50, db=None))

    assert len(calls) == 1
    assert calls[0]["tags"] == "tenant:t1"
