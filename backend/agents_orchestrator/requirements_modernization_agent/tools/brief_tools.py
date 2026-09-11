"""Tools of the Requirements agent in migration-intent mode (Track 3).

Three things this agent does beyond talking (help/multi-track-agent-access-design.md,
Portfolio 2 row 1):

  record    the brief — validated against the required sections, stored on the run as
            `migration_intent_payload`, frozen as the next numbered version (the page's
            history and the unit the BA signs off), and returned as the document itself
            (so on an Orchestrator turn `dispatch` files it as a Deliverable exactly as
            recorded).
  pull      the legacy repository from the chat — listed through the connection wired
            to this stage, pulled read-only — into the turn's scope: the project's copy
            on the page, the conversation's own copy in the Orchestrator.
  read      the pulled legacy code (`modernization_common.legacy_code`): its profile
            first, then any file — so the agent states the current stack instead of
            asking for it.
  export    the brief as a document, recorded as a draft artifact of this stage — the
            BA submitting it for approval is the Sign-off that baselines it.
  board     read the connected board's projects; WRITE an Epic and its child items for
            the migration — a Consequential action: the stage's owner must hold the
            approval AND have said yes on this very turn (`authorize_consequential`).
"""
from __future__ import annotations

import asyncio
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


def _enrich_from_code(brief: MigrationIntentArtifact) -> None:
    """Facts about TODAY come from the pulled code, not from the model: each module's
    path, runtime and support status, and each layer's worst status over its modules."""
    from agents_orchestrator.modernization_common.legacy_code import current_pull, current_scope  # noqa: PLC0415
    from agents_orchestrator.requirements_modernization_agent.brief import worst_status  # noqa: PLC0415

    project_id, run_id = current_scope()
    pull = current_pull(project_id, run_id) if project_id else None
    modules = ((pull or {}).get("profile") or {}).get("modules") or []
    if not modules:
        return
    by_name = {str(m.get("name", "")).lower(): m for m in modules}
    by_path = {str(m.get("path", "")).lower(): m for m in modules}

    def find(name: str, path: str = ""):
        key = (name or "").strip().lower()
        return (by_name.get(key) or by_path.get((path or "").strip().lower()) or by_path.get(key)
                or next((m for n, m in by_name.items() if key and (n.endswith(key) or key.endswith(n))), None))

    kept = []
    for change in brief.module_changes:
        m = find(change.module, change.path)
        if m is None:
            # "What changes in each module" is about the code's modules. A database or
            # hosting change is a part of the system (a layer), already in the change table.
            continue
        kept.append(change)
        change.path = change.path or str(m.get("path") or "")
        change.current = change.current or str(m.get("runtime") or "")
        status = str(m.get("runtimeStatus") or "")
        if status in {"eol", "approaching", "legacy", "supported"}:
            change.current_status = status
    brief.module_changes = kept
    for layer in brief.layers:
        matched = [find(name) for name in layer.modules]
        statuses = [str(m.get("runtimeStatus") or "") for m in matched if m]
        if statuses:
            layer.current_status = worst_status(statuses) or layer.current_status


@tool
async def record_migration_intent(
    system_name: str = "",
    goal: str = "",
    drivers: list[dict] | None = None,
    business_drivers: list[str] | None = None,
    current_stack: str = "",
    current_description: str = "",
    target_stack: str = "",
    target_description: str = "",
    layers: list[dict] | None = None,
    recommendation_summary: str = "",
    recommendation_rationale: list[str] | None = None,
    alternatives: list[dict] | None = None,
    recommended_by: str = "agent",
    module_changes: list[dict] | None = None,
    trade_offs: list[dict] | None = None,
    in_scope: list[str] | None = None,
    out_of_scope: list[str] | None = None,
    constraints: list[str] | None = None,
    deadline: str = "",
    budget: str = "",
    milestones: list[dict] | None = None,
    success_criteria: list[str] | None = None,
    success_measures: list[dict] | None = None,
    stakeholders: list[dict] | None = None,
    assumptions: list[str] | None = None,
    risks: list[str] | None = None,
    open_questions: list[str] | None = None,
    legacy_repository_url: str = "",
    legacy_repository_name: str = "",
    legacy_repository_project: str = "",
    legacy_repository_provider: str = "",
) -> str:
    """Record the migration-intent brief once the user has confirmed it.

    Required: system_name, why (drivers or business_drivers), today (layers or
    current_stack), the target (layers or target_stack), in_scope, constraints,
    success_criteria. If any is missing the brief is NOT recorded and the reply names
    what is still needed. Re-record the whole brief to revise; the newest brief wins.

    From the USER (never invent): drivers, in/out of scope, constraints, deadline, budget,
    milestones, success criteria and measures, stakeholders, assumptions, risks.
    From the CODE: today's modules and runtimes (support status is filled in from the
    pulled code automatically). From YOU, labelled as a recommendation unless the user
    set it: the target, the change per layer and per module, the trade-offs.

    Keep every label short — the brief is read at a glance: layer names in 2–4 words,
    current/target in about six words joined with " · ", ONE target per layer (no "or"),
    implementation detail only in module_changes[].changes.

    Shapes:
      goal: one sentence — the end state and why it matters.
      drivers: [{"category": "end_of_support|security|cost|skills|compliance|performance|other",
                 "title": "short headline", "detail": "one sentence, the user's facts"}]
      layers: [{"layer": "Settlement batch", "current": "Java 7 + Quartz 2.2",
                "target": "Java 21 + Spring Batch 5",
                "change_type": "upgrade|rewrite|replatform|replace|retire|keep|new",
                "modules": ["claimtrack-batch"]}]   one row per part of the system,
                including hosting, database and CI/CD when they change.
      recommendation_summary: two or three sentences — the recommended target and why.
      recommendation_rationale: ["one line per reason, tied to a code finding or a
                                  user reason/constraint"]
      alternatives: [{"option": "...", "why_not": "..."}]
      recommended_by: "agent" when you recommended it, "user" when they dictated it.
      module_changes: [{"module": "claimtrack-web", "target": "Java 21 + Spring Boot 3.3",
                        "change_type": "upgrade", "effort": "low|medium|high",
                        "changes": ["concrete change 1", "concrete change 2"]}]
      trade_offs: [{"decision": "Rebuild the portal in React", "gain": "...", "cost": "..."}]
      milestones: [{"date": "2027-06-30", "label": "Dallas data-centre exit",
                    "kind": "start|freeze|compliance|deadline|cutover|decommission|other"}]
      success_measures: [{"metric": "API p95 latency", "current": "~800 ms", "target": "<= 300 ms"}]
      stakeholders: [{"name": "...", "role": "..."}]
    """
    layer_items = layers or []
    driver_items = drivers or []
    if not business_drivers and driver_items:
        business_drivers = [
            str(d.get("title") or "").strip() + (f": {d['detail']}" if d.get("detail") else "")
            for d in driver_items if isinstance(d, dict) and (d.get("title") or d.get("detail"))
        ]
    if not current_stack and layer_items:
        current_stack = "; ".join(f"{d.get('layer')}: {d.get('current')}" for d in layer_items
                                  if isinstance(d, dict) and d.get("current"))
    if not target_stack and layer_items:
        target_stack = "; ".join(f"{d.get('layer')}: {d.get('target')}" for d in layer_items
                                 if isinstance(d, dict) and d.get("target"))
    if not success_criteria and success_measures:
        success_criteria = [f"{m.get('metric')}: {m.get('target')}" for m in success_measures
                            if isinstance(m, dict) and m.get("metric")]
    has_recommendation = bool(recommendation_summary or recommendation_rationale or alternatives)
    try:
        brief = MigrationIntentArtifact(
            system_name=system_name.strip(),
            goal=goal.strip(),
            drivers=driver_items,
            business_drivers=business_drivers or [],
            current_state={"stack": current_stack.strip(), "description": current_description.strip()},
            target_state={"stack": target_stack.strip(), "description": target_description.strip()},
            layers=layer_items,
            recommendation=({"summary": recommendation_summary.strip(),
                             "rationale": recommendation_rationale or [],
                             "alternatives": alternatives or [],
                             "recommended_by": recommended_by} if has_recommendation else None),
            module_changes=module_changes or [],
            trade_offs=trade_offs or [],
            in_scope=in_scope or [], out_of_scope=out_of_scope or [],
            constraints=constraints or [], deadline=deadline.strip(), budget=budget.strip(),
            milestones=milestones or [],
            success_criteria=success_criteria or [], success_measures=success_measures or [],
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
        err = exc.errors()[0]
        where = ".".join(str(x) for x in err.get("loc", ()))
        return f"The brief could not be recorded — `{where}` has the wrong shape: {err.get('msg')}"

    missing = missing_sections(brief)
    if missing:
        return ("NOT RECORDED YET — still missing: " + "; ".join(missing)
                + ". Ask the user for these (at most three questions at a time).")

    _enrich_from_code(brief)
    if brief.legacy_repository is None:
        from agents_orchestrator.modernization_common.legacy_code import (  # noqa: PLC0415
            current_scope,
            repository_for_brief,
        )

        pulled = repository_for_brief(*current_scope())
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
        if name.lower().endswith((".docx", ".pdf")):
            # The designed document — the same one the page's Word/PDF download gives.
            from agents_orchestrator.requirements_modernization_agent.brief_document import (  # noqa: PLC0415
                render_brief,
            )

            await asyncio.to_thread(render_brief, brief, path)
        else:
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


from agents_orchestrator.modernization_common.legacy_code import LEGACY_TOOLS, pull_tools  # noqa: E402

TOOLS = [
    record_migration_intent,
    export_migration_brief,
    list_board_projects,
    create_migration_work_items,
    *pull_tools(STAGE),
    *LEGACY_TOOLS,
]
