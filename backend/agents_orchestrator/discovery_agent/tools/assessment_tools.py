"""Assessing the cloned legacy repository, and reporting on it.

`assess_legacy_repository` is where the work happens: it runs the vulnerability
scanner, calls the deterministic analysis (`analysis/assessment.py`), stores the
result on the run as `discovery_artifacts`, and returns the assessment as a markdown
REPORT. Returning the document (rather than a JSON digest) is deliberate: on an
Orchestrator turn, `dispatch` files a tool result that is a document as a Deliverable,
so the report reaches the Deliverables panel exactly as computed — not as the model's
retelling of it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from langchain_core.tools import tool

from agents_orchestrator.discovery_agent.analysis.assessment import (
    assess_repository,
    assessment_markdown,
)
from agents_orchestrator.discovery_agent.tools.repo_tools import session

logger = logging.getLogger(__name__)

STAGE = "discovery"
FILE_SEGMENT = "discovery_agent"
#: Modules listed in the chat/deliverable copy of the report; the export lists all.
_REPORT_MODULE_LIMIT = 25


def _no_checkout() -> str:
    return ("No legacy code is pulled for this project and none is checked out in this "
            "conversation. Ask the user to press **Pull legacy code** on the page, or call "
            "list_legacy_repositories and clone_legacy_repository.")


def _ensure_checkout(s) -> bool:
    """True when this conversation has code to assess — adopting the project's pulled
    legacy code when the conversation has not cloned anything itself."""
    if s.work_dir and os.path.isdir(s.work_dir):
        return True
    from agents_orchestrator.discovery_agent.tools.repo_tools import adopt_project_checkout  # noqa: PLC0415
    from agents_orchestrator.modernization_common.legacy_code import current_pull  # noqa: PLC0415
    from config.ws_helper import get_project_id  # noqa: PLC0415

    project_id = str(get_project_id() or "")
    pull = current_pull(project_id) if project_id else None
    if not pull:
        return False
    adopt_project_checkout(s, project_id, pull)
    return True


def _scan_vulnerabilities(work_dir: str) -> tuple[list[dict], dict]:
    """Trivy over the checkout. Degrades to `unavailable` rather than failing the
    assessment — an assessment without CVEs is still a planning baseline, and says so."""
    try:
        from agents_orchestrator.security_agent.tools.trivy_tool import run_trivy_scan  # noqa: PLC0415

        # Offline: declared dependencies only, no Maven Central lookups — see trivy_tool.
        result = json.loads(run_trivy_scan.invoke({"target_path": work_dir, "offline": True}))
    except Exception as exc:  # noqa: BLE001
        return [], {"trivy": "error", "note": f"{type(exc).__name__}"}
    status = result.get("status", "error")
    if status != "ok":
        return [], {"trivy": status if status in {"unavailable", "error"} else "error",
                    "note": str(result.get("message") or "")[:300]}
    return list(result.get("findings") or []), {"trivy": "ok", "note": f"{result.get('findings_count', 0)} finding(s)."}


async def _persist(artifacts: dict) -> str:
    """Store the assessment on this turn's run. Returns a short status line."""
    from config.ws_helper import get_run_id, get_session_id, get_tenant_id  # noqa: PLC0415

    run_id, tenant_id = get_run_id(), get_tenant_id()
    if not run_id:
        return "Not saved: this conversation is not attached to a run."
    try:
        from shared.models.artifacts import DiscoveryArtifact  # noqa: PLC0415
        from shared.services.artifact_service import persist_artifact  # noqa: PLC0415

        payload = DiscoveryArtifact(**artifacts).model_dump()
        await persist_artifact(str(run_id), STAGE, payload, tenant_id=tenant_id or None)
    except Exception as exc:  # noqa: BLE001 — the user still gets the report
        logger.exception("discovery: persisting the assessment failed (session=%s)", get_session_id())
        return f"Not saved ({type(exc).__name__}) — the report below is still complete."
    from agents_orchestrator.modernization_common.versions import freeze_version, saved_line  # noqa: PLC0415

    version = await freeze_version(STAGE, payload)
    return saved_line("Saved to the project as the current assessment.", version, "assessment")


@tool
async def assess_legacy_repository(target_stack: str = "") -> str:
    """Assess the cloned legacy repository: inventory, dependency graph, end-of-life and
    vulnerable dependencies, and a migration-risk score and tier for every module.

    Args:
        target_stack: The stack the migration targets, from the migration-intent brief
            (e.g. ".NET 8", "Java 21 / Spring Boot"). Decides whether codemod tooling
            can apply (same language) or a rewrite is needed. Empty if not yet known.
    """
    s = session()
    if not _ensure_checkout(s):
        return _no_checkout()
    vulnerabilities, scanners = await asyncio.to_thread(_scan_vulnerabilities, s.work_dir)
    artifacts = await asyncio.to_thread(
        lambda: assess_repository(
            s.work_dir,
            vulnerabilities=vulnerabilities,
            repository={"url": s.repo_url, "branch": s.branch, "commit": s.commit,
                        "provider": s.provider, "name": s.repo_name},
            target_stack=target_stack,
            scanners=scanners,
        )
    )
    s.assessment = artifacts
    saved = await _persist(artifacts)
    report = assessment_markdown(artifacts, max_modules=_REPORT_MODULE_LIMIT)
    return f"{report}\n\n_{saved}_"


@tool
async def get_module_detail(module: str) -> str:
    """Everything the assessment knows about one module: runtime, every dependency
    with its status, what it depends on and what depends on it, and each risk factor.

    Args:
        module: The module's name as it appears in the assessment.
    """
    s = session()
    if not s.assessment:
        return "No assessment yet — call assess_legacy_repository first."
    modules = s.assessment.get("modules") or []
    match = next((m for m in modules if m["name"].lower() == (module or "").strip().lower()), None)
    if match is None:
        return f"No module named '{module}'. Modules: {', '.join(m['name'] for m in modules)}"
    return json.dumps(match, indent=1, default=str)


@tool
async def get_dependency_graph(module: str = "") -> str:
    """The dependency graph as edges. With `module`, only that module's edges."""
    s = session()
    if not s.assessment:
        return "No assessment yet — call assess_legacy_repository first."
    graph = s.assessment.get("dependency_graph") or {}
    edges = graph.get("edges") or []
    if module:
        node = f"module:{module.strip()}"
        edges = [e for e in edges if node.lower() in (e["from"].lower(), e["to"].lower())]
    lines = [f"{e['from']} -> {e['to']} ({e['type']}{', ' + e['version'] if e.get('version') else ''})"
             for e in edges]
    return "\n".join(lines) if lines else "No edges."


@tool
async def export_assessment_report(filename: str = "discovery_assessment.docx") -> str:
    """Export the full assessment report (every module) as a document the BA can put
    forward for sign-off. Format follows the extension: .docx, .pdf, .md."""
    s = session()
    if not s.assessment:
        return "No assessment yet — call assess_legacy_repository first."
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

    name = normalise_filename(filename, "discovery_assessment.docx")
    path = os.path.join(output_dir(FILE_SEGMENT), name)
    try:
        await render_document(assessment_markdown(s.assessment), path, title=name.rsplit(".", 1)[0])
    except ValueError:
        return f"Error: '{name}' has an unsupported extension. Supported: {supported_list()}"
    except Exception as exc:  # noqa: BLE001
        return f"Error generating '{name}' ({type(exc).__name__})."
    url = await announce_generated_file(FILE_SEGMENT, name, path, stage=STAGE)
    return export_result_message(
        name, url,
        ["It is saved as a draft of the Discovery & Assessment stage — submitting it for "
         "approval is how the BA accepts the assessment as the planning baseline."],
    )


from agents_orchestrator.discovery_agent.tools.repo_tools import (  # noqa: E402
    clone_legacy_repository,
    list_legacy_repositories,
)

from agents_orchestrator.modernization_common.legacy_code import LEGACY_TOOLS  # noqa: E402

TOOLS = [
    list_legacy_repositories,
    clone_legacy_repository,
    assess_legacy_repository,
    get_module_detail,
    get_dependency_graph,
    export_assessment_report,
    *LEGACY_TOOLS,
]
