"""The migration-intent brief and the Track 3 Requirements agent's tools."""
from __future__ import annotations

import pytest

from agents_orchestrator.requirements_modernization_agent.brief import (
    REQUIRED_SECTIONS,
    brief_markdown,
    missing_sections,
)
from agents_orchestrator.requirements_modernization_agent.tools import brief_tools
from config.ws_helper import set_session_id, set_tenant_id, set_user_id
from shared.models.artifacts import MigrationIntentArtifact

COMPLETE = {
    "system_name": "Billing",
    "business_drivers": [".NET Framework 4.5.2 is out of support", "IIS hosting cost"],
    "current_stack": ".NET Framework 4.5.2 WebForms on IIS",
    "target_stack": ".NET 8 on Azure App Service",
    "in_scope": ["Billing.Web", "Billing.Core"],
    "out_of_scope": ["Reporting database"],
    "constraints": ["Live before 31 March", "Invoice PDF layout must not change"],
    "success_criteria": ["Identical invoices for the recorded Q1 inputs", "p95 under 300 ms"],
    "legacy_repository_url": "https://dev.azure.com/acme/billing/_git/billing",
}


@pytest.fixture(autouse=True)
def _ctx(tmp_path):
    set_session_id(f"req-{tmp_path.name}")
    set_user_id("")
    set_tenant_id(None)
    yield
    brief_tools._LAST_BRIEF.clear()


def test_an_empty_brief_is_missing_every_required_section():
    assert missing_sections(MigrationIntentArtifact()) == [label for _, label in REQUIRED_SECTIONS]


def test_whitespace_is_not_an_answer():
    brief = MigrationIntentArtifact(system_name="  ", business_drivers=["  "])
    assert "System being modernized" in missing_sections(brief)
    assert "Why this modernization is happening" in missing_sections(brief)


def test_markdown_is_a_document_with_the_intake_sections():
    brief = MigrationIntentArtifact(
        system_name="Billing", business_drivers=["EOL"],
        current_state={"stack": ".NET Framework 4.5.2"}, target_state={"stack": ".NET 8"},
        in_scope=["Billing.Web"], constraints=["March"], success_criteria=["Same invoices"],
    )
    md = brief_markdown(brief)
    assert md.startswith("# Migration Intent Brief — Billing")
    for heading in ("## Why this modernization is happening", "## From → to", "## Scope",
                    "## Constraints", "## Success criteria", "## Legacy repository"):
        assert heading in md
    assert "| Target | .NET 8 |" in md


async def test_an_incomplete_brief_is_not_persisted(monkeypatch):
    calls: list = []

    async def fake_persist(brief):
        calls.append(brief)
        return "saved"

    monkeypatch.setattr(brief_tools, "_persist", fake_persist)
    partial = {k: v for k, v in COMPLETE.items() if k != "success_criteria"}
    out = await brief_tools.record_migration_intent.ainvoke(partial)
    assert out.startswith("NOT RECORDED YET")
    assert "Success criteria" in out
    assert calls == []


async def test_a_complete_brief_is_persisted_and_returned_as_the_document(monkeypatch):
    calls: list = []

    async def fake_persist(brief):
        calls.append(brief)
        return "Saved to the project as the current migration-intent brief."

    monkeypatch.setattr(brief_tools, "_persist", fake_persist)
    out = await brief_tools.record_migration_intent.ainvoke(COMPLETE)
    assert out.startswith("# Migration Intent Brief — Billing")
    assert len(calls) == 1 and calls[0].target_state.stack == ".NET 8 on Azure App Service"
    assert calls[0].legacy_repository.url.endswith("/_git/billing")
    assert calls[0].recorded_at


async def test_export_needs_a_recorded_brief():
    out = await brief_tools.export_migration_brief.ainvoke({})
    assert "record it first" in out


async def test_a_board_write_with_no_actor_is_refused_before_any_connector_call(monkeypatch):
    class _Board:
        display_name = "Azure DevOps"
        access_level = "read_write"

        async def write_adapter(self, *a, **k):
            raise AssertionError("must not write without an approving owner")

    monkeypatch.setattr("config.connectors.context.get_connector", lambda: _Board())
    out = await brief_tools.create_migration_work_items.ainvoke(
        {"project": "Billing", "epic_title": "Modernize Billing", "items_json": "[]"}
    )
    assert not out.startswith("Created")
    assert "needs a signed-in approver" in out


async def test_a_board_write_on_a_read_only_stage_names_the_grant(monkeypatch):
    class _ReadOnly:
        display_name = "Azure DevOps"
        access_level = "read"

    monkeypatch.setattr("config.connectors.context.get_connector", lambda: _ReadOnly())
    out = await brief_tools.create_migration_work_items.ainvoke(
        {"project": "Billing", "epic_title": "Modernize Billing"}
    )
    assert "cannot be used to write" in out


def test_prompt_is_migration_intent_not_stories():
    from agents_orchestrator.requirements_modernization_agent.prompts.migration_intent_prompt import (
        MIGRATION_INTENT_SYS_MESSAGE,
    )

    text = MIGRATION_INTENT_SYS_MESSAGE
    assert "MIGRATION-INTENT" in text
    assert "do NOT write user stories" in text
    assert "Success criteria" in text
    assert "Never invent" in text


def test_prompt_keeps_the_agents_own_examples_out_of_the_brief():
    """Found live (2026-09-10): asked for scope, the agent listed examples (data access
    layer, background jobs, reporting); the user named three items and three exclusions;
    the recorded brief carried the user's items PLUS the agent's examples, with "(if any)"
    and "unless ..." attached. The rule has to name that exact failure."""
    from agents_orchestrator.requirements_modernization_agent.prompts.migration_intent_prompt import (
        MIGRATION_INTENT_SYS_MESSAGE,
    )

    text = MIGRATION_INTENT_SYS_MESSAGE
    assert "Record ONLY what the user said or explicitly confirmed" in text
    assert "prompts, not answers" in text
    assert "(if any)" in text and "open questions" in text


def test_graph_compiles():
    from agents_orchestrator.requirements_modernization_agent.agents.intake import TOOLS, app

    assert app is not None
    assert {t.name for t in TOOLS} == {
        "record_migration_intent", "export_migration_brief", "list_board_projects",
        "create_migration_work_items",
        "get_legacy_code_profile", "list_legacy_files", "read_legacy_file", "search_legacy_code",
    }
