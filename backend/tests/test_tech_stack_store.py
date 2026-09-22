"""The resolver's cache and how agents reach it — the SQL itself is exercised live."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services import tech_stack_store as store  # noqa: E402
from shared.services.tech_stack import EffectiveTechStack, TechStack  # noqa: E402

NODE = EffectiveTechStack(
    TechStack("s1", "workspace", "w1", None, "Node + Next.js", categories={"languages": ["TypeScript"]}),
    "bu_default")


async def test_a_resolution_is_cached_until_invalidated(monkeypatch):
    store.invalidate_tech_stack_cache()
    fetch = AsyncMock(return_value=NODE)
    monkeypatch.setattr(store, "resolve_project_tech_stack", fetch)
    assert await store.resolve_project_tech_stack_cached("t1", "p1") is NODE
    assert await store.resolve_project_tech_stack_cached("t1", "p1") is NODE
    assert fetch.await_count == 1
    store.invalidate_tech_stack_cache("t1")
    await store.resolve_project_tech_stack_cached("t1", "p1")
    assert fetch.await_count == 2


async def test_invalidating_one_tenant_leaves_another_cached(monkeypatch):
    store.invalidate_tech_stack_cache()
    fetch = AsyncMock(return_value=NODE)
    monkeypatch.setattr(store, "resolve_project_tech_stack", fetch)
    await store.resolve_project_tech_stack_cached("t1", "p1")
    await store.resolve_project_tech_stack_cached("t2", "p9")
    store.invalidate_tech_stack_cache("t1")
    assert ("t1", "p1") not in store._CACHE and ("t2", "p9") in store._CACHE


async def test_a_failed_read_is_not_cached_and_says_so(monkeypatch):
    store.invalidate_tech_stack_cache()
    monkeypatch.setattr(store, "resolve_project_tech_stack", AsyncMock(side_effect=RuntimeError("db down")))
    eff = await store.resolve_project_tech_stack_cached("t1", "p2")
    assert eff.stack is None and "could not be read" in eff.warning
    assert ("t1", "p2") not in store._CACHE


async def test_agents_resolve_the_turns_project(monkeypatch):
    store.invalidate_tech_stack_cache()
    monkeypatch.setattr(store, "resolve_project_tech_stack", AsyncMock(return_value=NODE))
    import config.ws_helper as ws
    ws.set_tenant_id("t1")
    ws.set_project_id("p3")
    try:
        assert await store.current_project_tech_stack() is NODE
        ws.set_project_id(None)
        assert await store.current_project_tech_stack() is None
    finally:
        ws.set_tenant_id(None)
        ws.set_project_id(None)
