"""Tools of the Requirements agent in migration-intent mode (Track 3).

Three things this agent does beyond talking (help/multi-track-agent-access-design.md,
Portfolio 2 row 1):

  record    the brief — validated against the required sections, stored on the run as
            `migration_intent_payload`, frozen as the next numbered version (the page's
            history and the unit the BA signs off), and returned as the document itself
            (so on an Orchestrator turn `dispatch` files it as a Deliverable exactly as
            recorded).
  read      the project's pulled legacy code (`modernization_common.legacy_code`): its
            profile first, then any file — so the agent states the current stack
            instead of asking for it.
  export    the brief as a document, recorded as a draft artifact of this stage — the
            BA submitting it for approval is the Sign-off that baselines it.
  board     read the connected board's projects; WRITE an Epic and its child items for
            the migration — a Consequential action: the stage's owner must hold the
            approval AND have said yes on this very turn (`authorize_consequential`).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from langchain_core.tools import tool
from pydantic import ValidationError

from agents_orchestrator.requirements_modernization_agent.brief import (
    brief_markdown,
    missing_sections,
)
from shared.models.artifacts import LegacyRepository, MigrationIntentArtifact

logger = logging.getLogger(__name__)

STAGE = "requirements_modernization"
FILE_SEGMENT = "requirements_modernization_agent"

#: The last brief recorded in each conversation, for export. Keyed by session id.
_LAST_BRIEF: dict[str, MigrationIntentArtifact] = {}


def _session_key() -> str:
    from config.ws_helper import get_session_id  # noqa: PLC0415

    return str(get_session_id() or "default")


async def _persist(brief: MigrationIntentArtifact) -> str:
    from config.ws_helper import get_run_id, get_tenant_id  # noqa: PLC0415

    run_id = get_run_id()
    if not run_id:
        return "Not saved: this conversation is not attached to a run."
    try:
        from shared.services.artifact_service import persist_artifact  # noqa: PLC0415

        await persist_artifact(str(run_id), STAGE, brief.model_dump(), tenant_id=get_tenant_id() or None)
        return "Saved to the project as the current migration-intent brief."
    except Exception as exc:  # noqa: BLE001 — the user still has the brief in front of them
        logger.exception("migration intent: persisting the brief failed")
        return f"Not saved ({type(exc).__name__}) — the brief above is still complete."


@tool
async def record_migration_intent(
    system_name: str = "",
    business_drivers: list[str] | None = None,
    current_stack: str = "",
    current_description: str = "",
    target_stack: str = "",
    target_description: str = "",
    in_scope: list[str] | None = None,
    out_of_scope: list[str] | None = None,
    constraints: list[str] | None = None,
    success_criteria: list[str] | None = None,
    stakeholders: list[dict] | None = None,
    assumptions: list[str] | None = None,
    risks: list[str] | None = None,
    open_questions: list[str] | None = None,
    legacy_repository_url: str = "",
    legacy_repository_name: str = "",
    legacy_repository_project: str = "",
    legacy_repository_provider: str = "",
) -> str:
    """Record the migration-intent brief once the user has answered the required parts.

    Required: system_name, business_drivers (why), current_stack (from), target_stack
    (to), in_scope, constraints, success_criteria. If any is missing the brief is NOT
    recorded and the reply names what is still needed — ask the user for it; never
    invent an answer. Everything else is optional. `stakeholders` is a list of
    {"name": ..., "role": ...}. Re-record to revise; the newest brief wins.
    """
    try:
        brief = MigrationIntentArtifact(
            system_name=system_name.strip(),
            business_drivers=business_drivers or [],
            current_state={"stack": current_stack.strip(), "description": current_description.strip()},
            target_state={"stack": target_stack.strip(), "description": target_description.strip()},
            in_scope=in_scope or [], out_of_scope=out_of_scope or [],
            constraints=constraints or [], success_criteria=success_criteria or [],
            stakeholders=stakeholders or [], assumptions=assumptions or [],
            risks=risks or [], open_questions=open_questions or [],
            legacy_repository=(
                {"url": legacy_repository_url.strip(), "name": legacy_repository_name.strip(),
                 "project": legacy_repository_project.strip(),
                 "provider": legacy_repository_provider.strip()}
                if (legacy_repository_url or legacy_repository_name) else None
            ),
        )
    except ValidationError as exc:
        return f"The brief could not be recorded — a field has the wrong shape: {exc.errors()[0].get('msg')}"

    missing = missing_sections(brief)
    if missing:
        return ("NOT RECORDED YET — still missing: " + "; ".join(missing)
                + ". Ask the user for these (at most three questions at a time).")

    if brief.legacy_repository is None:
        from agents_orchestrator.modernization_common.legacy_code import repository_for_brief  # noqa: PLC0415
        from config.ws_helper import get_project_id  # noqa: PLC0415

        pulled = repository_for_brief(str(get_project_id() or ""))
        if pulled:
            brief.legacy_repository = LegacyRepository(**pulled)

    brief.recorded_at = datetime.now(timezone.utc).isoformat()
    brief.agent_session_id = _session_key()
    _LAST_BRIEF[_session_key()] = brief
    saved = await _persist(brief)
    from agents_orchestrator.modernization_common.versions import freeze_version, saved_line  # noqa: PLC0415

    version = await freeze_version(STAGE, brief.model_dump())
    return f"{brief_markdown(brief)}\n_{saved_line(saved, version, 'brief')}_"


@tool
async def export_migration_brief(filename: str = "migration_intent_brief.docx") -> str:
    """Export the recorded brief as a document the BA can submit for approval (the
    Sign-off that baselines it). Format follows the extension: .docx, .pdf, .md."""
    brief = _LAST_BRIEF.get(_session_key())
    if brief is None:
        return "No brief has been recorded in this conversation yet — record it first."
    from agents_orchestrator.modernization_common.files import (  # noqa: PLC0415
        announce_generated_file,
        output_dir,
    )
    from shared.tools.doc_export import (  # noqa: PLC0415
        export_result_message,
        normalise_filename,
        render_document,
        supported_list,
    )

    name = normalise_filename(filename, "migration_intent_brief.docx")
    path = os.path.join(output_dir(FILE_SEGMENT), name)
    try:
        await render_document(brief_markdown(brief), path, title=name.rsplit(".", 1)[0])
    except ValueError:
        return f"Error: '{name}' has an unsupported extension. Supported: {supported_list()}"
    except Exception as exc:  # noqa: BLE001
        return f"Error generating '{name}' ({type(exc).__name__})."
    url = await announce_generated_file(FILE_SEGMENT, name, path, stage=STAGE)
    return export_result_message(
        name, url,
        ["It is saved as a draft of this stage — submitting it for approval is how the BA "
         "baselines the migration intent."],
    )


async def _board(mode: str):
    """(connector, None) or (None, why-not). The Consequential check runs for writes,
    after the grant check, so a project that may not write at all hears about the
    grant rather than about approval."""
    try:
        from config.connectors.context import get_connector  # noqa: PLC0415

        conn = get_connector()
    except Exception:  # noqa: BLE001
        return None, ("No board is connected to this stage. A Project Admin can wire Azure "
                      "DevOps or Jira to the Migration Intent stage in project settings.")
    level = getattr(conn, "access_level", "__unscoped__")
    if level != "__unscoped__":
        from shared.authz.connector_access import label, permits  # noqa: PLC0415

        if not permits(level, mode):
            return None, (f"{conn.display_name} is {label(level)} for this stage, so it cannot "
                          f"be used to {mode} board items.")
    if mode == "write":
        from shared.authz.consequential import authorize_consequential  # noqa: PLC0415

        ok, why = await authorize_consequential(
            STAGE,
            action="Writing the migration work items to the project board",
            ask="ask the user to confirm the Epic and items you are about to create, listing them.",
        )
        if not ok:
            return None, why
    return conn, None


@tool
async def list_board_projects() -> str:
    """List the projects on the board connected to this stage."""
    conn, err = await _board("read")
    if err:
        return err
    try:
        projects = await conn.read_adapter("list_projects")
    except Exception as exc:  # noqa: BLE001
        return f"Error fetching projects: {type(exc).__name__}"
    if not projects:
        return "No projects found."
    return f"Projects on {conn.display_name}:\n" + "\n".join(
        f"- {p.get('name', '')}" + (f" ({p.get('key')})" if p.get("key") else "") for p in projects
    )


@tool
async def create_migration_work_items(project: str, epic_title: str, items_json: str = "[]") -> str:
    """Create the migration Epic and its child items on the board. CONSEQUENTIAL: first
    show the user exactly what will be created and ask; call this only after they say yes.

    Args:
        project: The board project name.
        epic_title: Title of the Epic for the modernization (e.g. "Modernize Billing to .NET 8").
        items_json: JSON array of {"title", "type" (default "Feature"), "description"}.
    """
    try:
        items = json.loads(items_json or "[]")
        if not isinstance(items, list):
            raise ValueError
    except ValueError:
        return 'items_json must be a JSON array like [{"title": "...", "description": "..."}].'

    conn, err = await _board("write")
    if err:
        return err
    brief = _LAST_BRIEF.get(_session_key())
    try:
        epic = await conn.write_adapter(
            "create_item", project=project, item_type="Epic", title=epic_title,
            description=brief_markdown(brief) if brief else "", acceptance_criteria="", parent_id="",
        )
    except Exception as exc:  # noqa: BLE001
        return f"The Epic could not be created: {type(exc).__name__}: {str(exc)[:200]}"
    epic_id = str(epic.get("work_item_id") or epic.get("id") or "")
    lines = [f"Created Epic #{epic_id}: {epic_title}"]
    for item in items:
        title = str((item or {}).get("title") or "").strip()
        if not title:
            continue
        kind = str(item.get("type") or "Feature")
        try:
            wi = await conn.write_adapter(
                "create_item", project=project, item_type=kind, title=title,
                description=str(item.get("description") or ""), acceptance_criteria="",
                parent_id=epic_id,
            )
            lines.append(f"- Created {kind} #{wi.get('work_item_id') or wi.get('id', '?')}: {title} (child of #{epic_id})")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"- FAILED {kind} '{title}': {type(exc).__name__}")
    return "\n".join(lines)


from agents_orchestrator.modernization_common.legacy_code import LEGACY_TOOLS  # noqa: E402

TOOLS = [
    record_migration_intent,
    export_migration_brief,
    list_board_projects,
    create_migration_work_items,
    *LEGACY_TOOLS,
]
