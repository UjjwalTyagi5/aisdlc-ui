"""A stage gets the connector tools its project granted it — and only those.

The bug being pinned: reach was per-project data and wiring was per-agent code, so a
project could grant a connector read-write to a stage whose agent had no tool for it.
The PM agent's "this platform only publishes to SharePoint" was that, exactly.
"""
from __future__ import annotations

import pytest

from shared.tools.stage_tools import tools_for_stage, wired_kinds


class _Ctx:
    """Patches the session contextvars and the grant lookup in one place."""

    def __init__(self, monkeypatch, *, tenant="t1", project="p1", levels=None):
        import shared.tools.stage_tools as mod

        monkeypatch.setattr("config.ws_helper.get_tenant_id", lambda: tenant)
        monkeypatch.setattr("config.ws_helper.get_project_id", lambda: project)

        async def _level(kind, tenant_id, project_id, agent_id):
            return (levels or {}).get(kind)

        monkeypatch.setattr(mod, "_granted_level", _level)


@pytest.mark.unit
def test_the_registry_only_lists_kinds_with_a_factory():
    """The picker offers these. A kind here without tools is a setting that cannot
    take effect — which is the whole defect this module exists to remove."""
    assert "confluence" in wired_kinds()
    assert "sharepoint" in wired_kinds()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_ungranted_connector_contributes_nothing(monkeypatch):
    _Ctx(monkeypatch, levels={})
    assert await tools_for_stage("plan", "plan") == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_write_grants_both_halves(monkeypatch):
    _Ctx(monkeypatch, levels={"confluence": "read_write"})
    names = {t.name for t in await tools_for_stage("plan", "plan")}
    assert "publish_approved_to_confluence" in names   # write
    assert "list_confluence_pages" in names            # read


@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_only_withholds_the_write_tools(monkeypatch):
    """THE POINT OF FILTERING RATHER THAN GUARDING. A write tool bound under a
    read-only grant makes the model offer the action, attempt it, and be refused —
    three wasted steps. Read-only should simply not offer to publish."""
    _Ctx(monkeypatch, levels={"confluence": "read"})
    names = {t.name for t in await tools_for_stage("plan", "plan")}
    assert "list_confluence_pages" in names
    assert "publish_approved_to_confluence" not in names
    assert "create_confluence_space" not in names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_only_withholds_the_read_tools(monkeypatch):
    """`read` and `write` are incomparable — write must not imply read.
    See the lattice note in shared/authz/connector_access.py."""
    _Ctx(monkeypatch, levels={"confluence": "write"})
    names = {t.name for t in await tools_for_stage("plan", "plan")}
    assert "publish_approved_to_confluence" in names
    assert "list_confluence_pages" not in names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_granted_connectors_both_appear(monkeypatch):
    _Ctx(monkeypatch, levels={"confluence": "read_write", "sharepoint": "read_write"})
    names = {t.name for t in await tools_for_stage("plan", "plan")}
    assert "publish_approved_to_confluence" in names
    assert "publish_approved_to_sharepoint" in names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_project_means_no_connector_tools(monkeypatch):
    """Agent-studio previews and health probes run without a project."""
    _Ctx(monkeypatch, project="", levels={"confluence": "read_write"})
    assert await tools_for_stage("plan", "plan") == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failing_lookup_yields_no_tools_and_does_not_raise(monkeypatch):
    """An agent with fewer tools still answers. One that raises here is a dead
    conversation — see the module docstring."""
    import shared.tools.stage_tools as mod

    monkeypatch.setattr("config.ws_helper.get_tenant_id", lambda: "t1")
    monkeypatch.setattr("config.ws_helper.get_project_id", lambda: "p1")

    async def _boom(kind, tenant_id, project_id, agent_id):
        raise RuntimeError("database is having a day")

    monkeypatch.setattr(mod, "_granted_level", _boom)
    assert await tools_for_stage("plan", "plan") == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_stage_is_passed_through_to_the_factory(monkeypatch):
    """`agent_id` and `stage` must reach the factory, or every stage would publish
    the same stage's documents."""
    seen = {}
    import shared.tools.stage_tools as mod

    def _fake_factory(agent_id, stage):
        seen["args"] = (agent_id, stage)
        return []

    monkeypatch.setitem(
        mod._SPECS, "confluence",
        lambda: mod.ConnectorToolSpec(factory=_fake_factory, write_tools=frozenset()),
    )
    _Ctx(monkeypatch, levels={"confluence": "read_write"})
    await tools_for_stage("requirements", "requirements")
    assert seen["args"] == ("requirements", "requirements")


# ── The PM agent resolves its connectors from the grant ──────────────────────
#
# The agent whose refusal started this. Its static list must no longer name a
# connector, and both the model binding AND the dispatcher must consult the grant —
# binding a tool the dispatcher does not know produces "Unknown tool" after the model
# has already decided to use it.


@pytest.mark.unit
def test_pm_agent_no_longer_hardcodes_connector_tools():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "agents_orchestrator/pm_agent/agents/schedule.py").read_text(encoding="utf-8")
    assert "make_confluence_tools" not in src
    assert "make_sharepoint_tools" not in src
    assert "tools_for_stage" in src


@pytest.mark.unit
def test_pm_agent_offers_and_dispatches_the_same_set():
    """Two call sites, one list. If `bind_tools` sees the granted tools and the
    dispatcher does not, the model calls a tool that comes back 'Unknown tool'."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "agents_orchestrator/pm_agent/agents/schedule.py").read_text(encoding="utf-8")
    assert src.count('tools_for_stage("plan", "plan")') == 2


@pytest.mark.unit
def test_the_pm_agents_static_list_still_has_its_own_tools():
    """Guards the guard: an empty list would pass the assertions above."""
    import agents_orchestrator.pm_agent.agents.schedule as mod

    names = {t.name for t in mod.tools}
    assert "list_sprints" in names
    assert not {n for n in names if "confluence" in n or "sharepoint" in n}


# ── The picker is told which connectors can actually be acted on ─────────────


@pytest.mark.unit
def test_wired_and_unwired_do_not_overlap():
    """A kind claiming both states would make the picker's annotation arbitrary."""
    from shared.tools.stage_tools import UNWIRED_KINDS

    assert not (wired_kinds() & UNWIRED_KINDS)


@pytest.mark.unit
def test_every_catalogue_kind_is_classified():
    """A connector offered by the catalogue but in neither set is unclassified — the
    picker would silently treat it as usable, which is the defect this closes."""
    from shared.routers.connectors import _CATALOG_KINDS
    from shared.tools.stage_tools import UNWIRED_KINDS

    unclassified = set(_CATALOG_KINDS) - wired_kinds() - UNWIRED_KINDS
    assert not unclassified, f"add these to stage_tools: {sorted(unclassified)}"


@pytest.mark.unit
def test_the_registry_kinds_are_all_real_catalogue_entries():
    """Guards the other direction: a typo'd kind would silently never match."""
    from shared.routers.connectors import _CATALOG_KINDS
    from shared.tools.stage_tools import UNWIRED_KINDS

    assert (wired_kinds() | UNWIRED_KINDS) <= set(_CATALOG_KINDS)


@pytest.mark.unit
def test_confluence_counts_as_wired_now():
    """The connector at the centre of this: granted, and previously unusable."""
    assert "confluence" in wired_kinds()
