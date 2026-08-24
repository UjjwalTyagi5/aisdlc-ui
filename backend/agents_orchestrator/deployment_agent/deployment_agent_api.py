"""Deployment Agent — FastAPI router (WebSocket + REST).

Receives codebase.zip + testing.zip, runs a deterministic pipeline using Claude
to produce: risk analysis, deployment validation, Dockerfile, README, and Docker
instructions. Persists DeploymentArtifacts to Postgres and emits a HANDOFF sentinel
so the Monitoring agent can pick up context.
"""

import base64
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from agents_orchestrator.deployment_agent.agents.pipeline_app import (
    extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files,
    combine_content,
    generate_deployment_plan, generate_risk_analysis, generate_deployment_validation,
    extract_deployment_decision,
    generate_dockerfile, generate_docker_compose, generate_cicd_pipeline,
    generate_readme, generate_docker_instructions, generate_rollback_plan,
    write_all_outputs, print_pipeline_summary, update_work_items_to_deployed,
    resolve_ado_target, clone_azure_repo, detect_tech_stack,
    generate_deployment_files, commit_and_push_to_ado, trigger_azure_pipeline,
    trigger_deployment_pipeline, trigger_github_actions_workflow,
    verify_testing_gate,
)
from config import sdlcSettings
from shared.services.conversation_service import persist_turn
from config.agent_context import build_agent_input_text, parse_pipeline_context, set_agent_folder
from config.connection_manager import manager
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE
from shared.services.agent_session_store import patch_session_artifacts
from config.orchestrator_state_client import fetch_session_artifacts
from config.websocket_utils import set_websocket_context
from config.ws_helper import set_session_id, set_user_id, set_provider_kind
from shared.errors import classify_error
from shared.models.deployment import DeploymentArtifacts

esett = sdlcSettings()

logger = logging.getLogger("deployment_agent")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_fmt)
logger.addHandler(_handler)

deployment_router_orchestrator = APIRouter()


# ── Routing helpers ───────────────────────────────────────────────────────────

def _required_files_for_task(task: str) -> List[str]:
    if task in ("risk_analysis", "deployment_validation", "full_pipeline", "create_deployment_files"):
        return ["codebase", "testing"]
    if task in ("dockerfile", "readme"):
        return ["codebase"]
    return []


def _has_required_files(pipeline_files: Dict[str, Optional[str]], needed: List[str]) -> Tuple[bool, List[str]]:
    missing = []
    if "codebase" in needed and not pipeline_files.get("codebase_zip"):
        missing.append("codebase")
    if "testing" in needed and not pipeline_files.get("testing_zip"):
        missing.append("testing")
    return len(missing) == 0, missing


def _route_task(user_message: str, pipeline_files: Dict[str, Optional[str]]) -> Dict[str, Any]:
    msg = user_message.lower().strip()
    deployment_kw = {"create deployment files", "generate deployment files", "all deployment files"}
    ado_deploy_kw = {"deploy from azure", "deploy to azure", "trigger pipeline", "trigger deployment", "deploy via ado", "ado deploy", "azure pipeline"}
    has_zip = bool(pipeline_files.get("codebase_zip"))

    if any(kw in msg for kw in ado_deploy_kw):
        task, needs, label = "ado_deploy", [], "Running Azure DevOps deployment: clone repo → generate files → push → trigger pipeline."
    elif msg.strip() in {"deploy", "trigger", "ship", "release"} and not has_zip:
        task, needs, label = "ado_deploy", [], "Running Azure DevOps deployment: clone repo → generate files → push → trigger pipeline."
    elif any(kw in msg for kw in deployment_kw):
        task, needs = "full_pipeline", ["codebase", "testing"]
        label = "Creating all deployment files: deployment plan, risk analysis, validation, Dockerfile, compose, CI/CD, README, instructions, rollback."
    elif "dockerfile" in msg and "readme" not in msg:
        task, needs, label = "dockerfile", ["codebase"], "Generating Dockerfile."
    elif "readme" in msg and "dockerfile" not in msg:
        task, needs, label = "readme", ["codebase"], "Generating README.md."
    elif "validation" in msg:
        task, needs, label = "deployment_validation", ["codebase", "testing"], "Running deployment validation."
    elif "risk" in msg and "analysis" in msg:
        task, needs, label = "risk_analysis", ["codebase", "testing"], "Running risk analysis."
    elif "docker instructions" in msg:
        task, needs, label = "docker_instructions", [], "Generating Docker deployment instructions."
    elif has_zip:
        task, needs, label = "full_pipeline", ["codebase", "testing"], "Running full deployment pipeline."
    else:
        task, needs, label = "ado_deploy", [], "No files uploaded — running Azure DevOps deployment from connected repo."

    ok, missing = _has_required_files(pipeline_files, needs)
    if not ok:
        label = f"Cannot proceed — missing required files: {', '.join(missing)}. Please upload them."
    return {"task": task, "required_files": needs, "assistant_message": label}


def _build_step_plan(task: str) -> List[Any]:
    if task == "ado_deploy":
        return [
            verify_testing_gate,  # Phase 9.3 — block if orchestrator-driven and tests failed
            resolve_ado_target,
            clone_azure_repo,
            detect_tech_stack,
            generate_deployment_files,
            commit_and_push_to_ado,
            # Provider-aware: reads deploy_via from state at execution time and
            # delegates to the Azure Pipelines or GitHub Actions trigger. Defaults to
            # Azure Pipelines when unset, so existing ADO runs are unchanged.
            trigger_deployment_pipeline,
            write_all_outputs,
            print_pipeline_summary,
        ]
    if task == "risk_analysis":
        return [extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files, combine_content, generate_risk_analysis, write_all_outputs, print_pipeline_summary]
    if task == "deployment_validation":
        return [extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files, combine_content, generate_deployment_validation, write_all_outputs, print_pipeline_summary]
    if task == "dockerfile":
        return [extract_codebase_zip, read_codebase_files, combine_content, generate_dockerfile, write_all_outputs, print_pipeline_summary]
    if task == "readme":
        return [extract_codebase_zip, read_codebase_files, combine_content, generate_readme, write_all_outputs, print_pipeline_summary]
    if task == "docker_instructions":
        return [combine_content, generate_docker_instructions, write_all_outputs, print_pipeline_summary]
    # full_pipeline
    return [
        extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files,
        combine_content,
        generate_deployment_plan,
        generate_risk_analysis, generate_deployment_validation, extract_deployment_decision,
        generate_dockerfile, generate_docker_compose, generate_cicd_pipeline,
        generate_readme, generate_docker_instructions, generate_rollback_plan,
        write_all_outputs, print_pipeline_summary, update_work_items_to_deployed,
    ]


def _pipeline_summary(state: Dict[str, Any]) -> str:
    """Phase 9.5 — deterministic, evidence-bound deployment summary.

    Each row only emits when its underlying step actually ran. Mirrors the
    block-joined paragraph pattern from testing-agent's qa_summary.py
    (Phase 8.6/8.9b) so CommonMark renders cleanly.

    On blocked deployments only Mode / Result / Testing gate / Next step
    appear — the rest are inapplicable. On successful runs the full
    pipeline shows.
    """
    blocks: list[str] = []

    # Mode — derive from state signals (orchestrator_driven is the strongest)
    if state.get("orchestrator_driven"):
        mode = "orchestrator-driven ADO"
    elif state.get("ado_project") or state.get("deployment_request", {}).get("ado_project"):
        mode = "standalone ADO"
    else:
        mode = "standalone upload"

    # Header — distinguish blocked / failed / completed
    block_status = state.get("status") == "blocked"
    push_failed = state.get("push_status") == "failed"
    pipeline_trigger_failed = state.get("pipeline_trigger_status") == "failed"
    if block_status:
        blocks.append("## Deployment blocked")
    elif push_failed:
        blocks.append("## Deployment failed")
    else:
        blocks.append("## Deployment completed")

    # Result — most important line
    has_pipeline = bool(state.get("pipeline_run_id"))
    pushed = state.get("push_status") == "pushed"
    no_changes = state.get("push_status") == "no_changes"

    if block_status:
        result = f"⚠️ Blocked — {state.get('block_reason', 'see details below')}"
    elif push_failed:
        result = "❌ Failed at push"
    elif pipeline_trigger_failed and (pushed or no_changes):
        result = "✅ Deployment assets prepared; Azure Pipeline trigger returned a warning"
    elif pipeline_trigger_failed:
        result = "⚠️ Azure Pipeline trigger returned a warning"
    elif has_pipeline and (pushed or no_changes):
        result = "✅ Pushed + pipeline triggered" if pushed else "✅ No changes — pipeline triggered against existing tip"
    elif pushed:
        result = "✅ Pushed (pipeline trigger pending or skipped)"
    elif no_changes:
        result = "✅ No changes to push (deployment files already up to date)"
    elif state.get("dockerfile_content") or state.get("cicd_pipeline_azure"):
        result = "✅ Deployment files generated locally"
    else:
        result = "⚠️ Pipeline ran with no measurable outcome"

    blocks.append(f"**Mode:** {mode}")
    blocks.append(f"**Result:** {result}")

    # Testing gate (only meaningful in orch-driven mode)
    gate = state.get("testing_gate", "")
    if gate:
        if gate == "passed":
            te = (state.get("testing_artifacts") or {}).get("test_execution") or {}
            t_total = te.get("total", 0)
            t_passed = te.get("passed", 0)
            blocks.append(f"**Testing gate:** ✅ passed ({t_passed}/{t_total} tests)")
        elif gate == "skipped_standalone":
            blocks.append("**Testing gate:** ⏭ skipped (standalone-ADO mode)")
        else:
            blocks.append(f"**Testing warning:** WARNING - {state.get('block_reason', gate)}")

    # If blocked, end with next step + return — rest of rows are irrelevant
    if block_status:
        blocks.append("**Next step:** Re-run testing successfully, then re-approve deployment.")
        return "\n\n".join(blocks)

    # ADO target (only when we resolved one)
    ado_project = state.get("ado_project", "")
    ado_repo = state.get("ado_repo", "")
    ado_branch = state.get("ado_branch", "")
    if ado_project or ado_repo or ado_branch:
        ado_lines = ["**ADO target:**"]
        if ado_project:
            ado_lines.append(f"- Project: `{ado_project}`")
        if ado_repo:
            ado_lines.append(f"- Repo: `{ado_repo}`")
        if ado_branch:
            ado_lines.append(f"- Branch: `{ado_branch}`")
        blocks.append("\n".join(ado_lines))

    # Detected stack
    stack = state.get("tech_stack", "")
    if stack:
        blocks.append(f"**Detected stack:** {stack}")

    # Files (created vs existing_skipped)
    created = state.get("created_files") or []
    skipped = state.get("existing_skipped") or []
    if created or skipped:
        file_lines = ["**Files:**"]
        for f in created:
            file_lines.append(f"- `{f}`: ✅ created")
        for f in skipped:
            file_lines.append(f"- `{f}`: ⏭ existing_skipped")
        blocks.append("\n".join(file_lines))

    # Commit
    commit_sha = state.get("commit_sha")
    push_status = state.get("push_status", "")
    if commit_sha:
        blocks.append(f"**Commit:** `{commit_sha}` — \"Deployment Agent: add missing deployment assets\"")
    elif push_status == "no_changes":
        blocks.append("**Commit:** ⏭ no changes to commit")

    # Push
    if push_status == "pushed" and ado_branch:
        blocks.append(f"**Push:** ✅ pushed to origin/{ado_branch}")
    elif push_status == "no_changes":
        blocks.append("**Push:** ⏭ skipped (no commit needed)")
    elif push_status == "failed":
        errors = state.get("errors") or []
        first_err = errors[0] if errors else "unknown error"
        blocks.append(f"**Push:** ❌ {first_err[:200]}")

    # Pipeline
    pipeline_url = state.get("pipeline_run_url", "")
    pipeline_id = state.get("pipeline_run_id", "")
    if pipeline_url:
        run_id_str = f" #{pipeline_id}" if pipeline_id else ""
        blocks.append(f"**Pipeline:** ✅ run{run_id_str} — {pipeline_url}")
    elif pipeline_trigger_failed:
        errors = state.get("errors") or []
        first_err = errors[-1] if errors else "trigger failed"
        blocks.append(f"**Pipeline:** ⚠️ trigger attempted; review ADO/resource configuration ({str(first_err)[:200]})")
    elif state.get("cicd_pipeline_azure") or state.get("existing_pipeline_yaml"):
        blocks.append("**Pipeline:** ⏭ skipped (no trigger attempted)")

    # Local-only deployment artifacts (when no ADO push)
    if not (ado_project or ado_repo or ado_branch):
        local_artifacts = []
        for key, label in [
            ("dockerfile_content", "Dockerfile"),
            ("docker_compose", "docker-compose.yml"),
            ("cicd_pipeline_azure", "azure-pipelines.yml"),
            ("readme_content", "README.md"),
            ("rollback_plan", "rollback_plan.md"),
        ]:
            if state.get(key):
                local_artifacts.append(label)
        if local_artifacts:
            blocks.append(f"**Local artifacts generated:** {', '.join(local_artifacts)}")

    # Errors (best-effort summary; full detail in Activity feed + artifact JSON)
    errors = state.get("errors") or []
    if errors:
        err_lines = [f"**Warnings ({len(errors)}):**"]
        for e in errors[:3]:
            err_lines.append(f"- {str(e)[:200]}")
        if len(errors) > 3:
            err_lines.append(f"- (+{len(errors)-3} more)")
        blocks.append("\n".join(err_lines))

    # Next step
    if has_pipeline:
        blocks.append("**Next step:** Deployment complete — pipeline is running. Check the ADO portal for build/run progress.")
    elif pipeline_trigger_failed and (pushed or no_changes):
        blocks.append("**Next step:** Deployment assets are in ADO. Review the pipeline/resource warning in ADO when ready.")
    elif pushed or no_changes:
        blocks.append("**Next step:** Pipeline trigger pending — check the ADO portal, or re-run deploy.")
    else:
        blocks.append("**Next step:** Review the warnings above and re-run when ready.")

    return "\n\n".join(blocks)


# ── Context enrichment ────────────────────────────────────────────────────────

async def _fetch_session_artifacts_safe(session_id: str) -> Dict[str, Any]:
    """Phase 9.3 — pull raw artifacts dict for both context-string building
    AND structured gate checks. Empty dict on any error so callers can
    treat 'missing' uniformly without try/except."""
    try:
        artifacts = await fetch_session_artifacts(session_id)
    except Exception:
        return {}
    return artifacts or {}


async def _build_session_context(session_id: str) -> str:
    """Fetch prior agent artifacts from Postgres and format them for the deployment prompts."""
    artifacts = await _fetch_session_artifacts_safe(session_id)
    if not artifacts:
        return ""

    lines: List[str] = []

    req = artifacts.get("requirements_payload") or {}
    if req:
        lines.append(f"[REQUIREMENTS — Project: {req.get('project', 'N/A')}]")
        stories = req.get("stories") or []
        for s in stories[:10]:
            title = s.get("title") or s.get("name") or str(s)
            lines.append(f"  • Story: {title}")
        if req.get("tech_stack"):
            lines.append(f"  Tech Stack: {req['tech_stack']}")
        if req.get("gap_report"):
            lines.append(f"  Gap Report: {req['gap_report'][:300]}")

    design = artifacts.get("design_artifacts") or {}
    if design:
        lines.append("\n[DESIGN ARTIFACTS]")
        for field in ("tech_stack", "adrs", "api_contract", "database_schema"):
            val = design.get(field, "")
            if val:
                lines.append(f"  {field.upper()}: {str(val)[:500]}")

    dev = artifacts.get("development_artifacts") or {}
    if dev:
        lines.append("\n[DEVELOPMENT ARTIFACTS]")
        for field in ("repo_url", "branch_name", "pr_url"):
            val = dev.get(field, "")
            if val:
                lines.append(f"  {field.upper()}: {str(val)[:300]}")
        test_results = dev.get("test_results") or []
        if test_results:
            lines.append(f"  TEST_RESULTS: {str(test_results)[:400]}")
        build_results = dev.get("build_results") or []
        if build_results:
            lines.append(f"  BUILD_RESULTS: {str(build_results)[:300]}")

    testing = artifacts.get("testing_artifacts") or {}
    if testing:
        lines.append("\n[TESTING ARTIFACTS]")
        status = testing.get("status")
        if status:
            lines.append(f"  STATUS: {status}")
        te = testing.get("test_execution") or {}
        if te:
            total = te.get("total", 0)
            passed = te.get("passed", 0)
            failed = te.get("failed", 0)
            lines.append(f"  TOTAL_TESTS: {total}  PASSED: {passed}  FAILED: {failed}")
            if total:
                lines.append(f"  PASS_RATE: {round(passed / total * 100, 1)}%")
        cov = testing.get("coverage") or {}
        if cov:
            cov_pct = cov.get("coverage_pct")
            if cov_pct is not None:
                lines.append(f"  COVERAGE: {cov_pct}%")
        summary_md = testing.get("summary_md", "")
        if summary_md:
            lines.append(f"  SUMMARY: {str(summary_md)[:300]}")

    return "\n".join(lines) if lines else ""


# ── Artifact persistence ──────────────────────────────────────────────────────

async def _persist_deployment_artifacts(session_id: str, pipeline_state: Dict[str, Any]) -> None:
    """Extract completed artifacts from pipeline state and persist via in-process Postgres store."""
    def _ok(v: str) -> bool:
        return bool(v) and not v.startswith(("⚠", "❌", "# ⚠", "# ❌"))

    def _v(key: str) -> Optional[str]:
        val = pipeline_state.get(key, "")
        return val if _ok(val) else None

    artifacts = DeploymentArtifacts(
        risk_analysis=_v("risk_report"),
        deployment_validation=_v("deploy_report"),
        dockerfile=_v("dockerfile_content"),
        readme=_v("readme_content"),
        docker_instructions=_v("docker_instructions"),
        deployment_plan=_v("deployment_plan"),
        docker_compose=_v("docker_compose"),
        cicd_pipeline_azure=_v("cicd_pipeline_azure"),
        cicd_pipeline_github=_v("cicd_pipeline_github"),
        rollback_plan=_v("rollback_plan"),
        deployment_decision=pipeline_state.get("deployment_decision") or None,
    )

    actually_completed = _deployment_completed_for_handoff(pipeline_state)
    has_pipeline = bool(pipeline_state.get("pipeline_run_id"))

    handoff_sentinel: Optional[str] = None
    last_handoff_event: Optional[Dict[str, Any]] = None
    batch_id = f"DEP-{uuid.uuid4().hex[:8].upper()}"
    decision = pipeline_state.get("deployment_decision", "")

    if actually_completed and (has_pipeline or pipeline_state.get("push_status") in ("pushed", "no_changes")):
        # Deployment is the terminal stage in this MVP — there is no
        # Monitoring agent wired in process_api.py. Emitting `to: monitoring`
        # made the orchestrator set pending_user_gate='monitoring' and the
        # frontend rendered a misleading "Ready to continue to monitoring?"
        # Approve banner that, if clicked, would 404 against the missing
        # /sdlc/agent/monitoring_orchestrator/chat/ endpoint.
        # Use `to: done` so the orchestrator clears the gate and the UI
        # shows no banner — deployment success message stays in chat.
        handoff_payload = {
            "to": "done",
            "batch_id": batch_id,
            "stage_completed": "deployment",
            "deployment_decision": decision,
            "context_keys": ["deployment_artifacts"],
            "triggered_by": "auto",
        }
        pipeline_url = pipeline_state.get("pipeline_run_url", "")
        if pipeline_url:
            handoff_payload["pipeline_run_url"] = pipeline_url
            handoff_payload["pipeline_run_id"] = pipeline_state.get("pipeline_run_id", "")
            handoff_payload["ado_project"] = pipeline_state.get("ado_project", "")
            handoff_payload["ado_repo"] = pipeline_state.get("ado_repo", "")
            handoff_payload["ado_branch"] = pipeline_state.get("ado_branch", "")
        handoff_sentinel = json.dumps(handoff_payload)
        last_handoff_event = {
            "to": "done",
            "batch_id": batch_id,
            "stage_completed": "deployment",
        }

    try:
        # Always persist artifacts so the user can inspect them; only persist
        # last_handoff_event + advance current_stage when we actually succeeded.
        patch_fields: Dict[str, Any] = {
            "deployment_artifacts": artifacts.model_dump(),
        }
        if last_handoff_event is not None:
            patch_fields["last_handoff_event"] = last_handoff_event
            patch_fields["current_stage"] = "done"
        tenant_id: Optional[str] = pipeline_state.get("tenant_id") or None
        await patch_session_artifacts(session_id, patch_fields, tenant_id=tenant_id)
        logger.info(
            "DeploymentArtifacts persisted for session %s (handoff=%s)",
            session_id, "emitted" if handoff_sentinel else "skipped",
        )
    except Exception as exc:
        logger.warning("AgentSession patch failed: %s", exc)

    return handoff_sentinel


def _deployment_completed_for_handoff(pipeline_state: Dict[str, Any]) -> bool:
    """True when deployment assets reached ADO and the pipeline was attempted."""
    if pipeline_state.get("status", "") == "blocked":
        return False
    if pipeline_state.get("push_status") == "failed":
        return False
    if pipeline_state.get("pipeline_run_id"):
        return True
    if pipeline_state.get("pipeline_trigger_status") == "failed":
        return pipeline_state.get("push_status") in ("pushed", "no_changes")
    return False


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@deployment_router_orchestrator.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(code=4401, reason='{"error": "invalid_or_expired_ticket", "detail": "Provide a valid single-use ticket from POST /auth/ws-ticket"}')
        return
    if AGENT_RUNTIME_MODE == "enterprise":
        expected_tenant = websocket.query_params.get("tenant_id", "")
        if expected_tenant and claims.get("tenant_id", "") != expected_tenant:
            await websocket.close(code=4403, reason='{"error": "tenant_mismatch", "detail": "Token tenant does not match requested tenant"}')
            return
    set_agent_folder("orchestrator")
    await manager.connect(websocket)
    user_id = claims.get("user_id", "")
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            session_id = message.get("session_id", str(uuid.uuid4()))
            set_session_id(session_id)
            set_user_id(user_id)
            set_websocket_context(manager, session_id)

            if message.get("type") == "user_message_with_files":
                if "provider_kind" in message:
                    set_provider_kind(message["provider_kind"])
                await _handle_user_message(
                    message, websocket, user_id, session_id,
                    tenant_id=claims.get("tenant_id", "") if claims else "",
                )
            elif message.get("type") == "clear_agents":
                await manager.clear_agents()
            elif message.get("type") == "session_cleanup":
                logger.info("Cleaning up session: %s", session_id)
            else:
                await manager.send_personal_message(json.dumps({"type": "echo", "message": data}), websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        manager.disconnect(websocket)


async def _handle_user_message(
    message: Dict[str, Any], websocket: WebSocket, user_id: str, session_id: str,
    tenant_id: str = "",
) -> None:
    try:
        # ── Save uploaded files ───────────────────────────────────────────────
        input_dir = os.path.join(esett.FILES, user_id, "orchestrator", session_id, "input")
        output_dir = os.path.join(esett.FILES, user_id, "orchestrator", session_id, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        pipeline_files: Dict[str, Optional[str]] = {"codebase_zip": None, "testing_zip": None}

        for f in message.get("files", []):
            fname, content = f.get("name", ""), f.get("content", "")
            if not fname or not content:
                continue
            try:
                binary = base64.b64decode(content)
                stem, ext = os.path.splitext(fname)
                save_path = os.path.join(input_dir, f"{stem}_{session_id}{ext}")
                with open(save_path, "wb") as fd:
                    fd.write(binary)
                lower = fname.lower()
                if any(kw in lower for kw in ("codebase", "source", "src")):
                    pipeline_files["codebase_zip"] = save_path
                elif any(kw in lower for kw in ("test", "report")):
                    pipeline_files["testing_zip"] = save_path
                await manager.send_agent_response("Deployment Agent", f"✅ File saved: {fname}", session_id)
            except Exception as exc:
                err = classify_error(exc, f"saving file {fname}")
                await manager.send_agent_response("Deployment Agent", f"❌ {err}", session_id)
                return

        # ── Route ─────────────────────────────────────────────────────────────
        pipeline_context = message.get("pipeline_context")
        orchestration_message = build_agent_input_text(
            conversation_context=message.get("conversation_context"),
            task_intent=message.get("task_intent"),
            pipeline_context=pipeline_context,
            pipeline_sections=("requirements", "development", "testing"),
        )
        user_text = message.get("text") or message.get("task_intent") or orchestration_message
        # Chat attachments (uploaded via POST /conversations/{id}/attachments) — persist
        # refs so a reopened session shows + can download them.
        _attachments = pipeline_context.get("attachments") if isinstance(pipeline_context, dict) else None
        # Persist the user turn to the conversation transcript (§11A) — best-effort.
        await persist_turn(
            session_id, "user", user_text, tenant_id=tenant_id or None, author_id=str(user_id),
            artifact_refs=_attachments or None,
        )
        dep_req = message.get("deployment_request", {})
        routed = _route_task(user_text, pipeline_files)
        # Persist the agent's conversational reply (sent in both branches below).
        await persist_turn(
            session_id, "agent", routed.get("assistant_message"),
            tenant_id=tenant_id or None, author_id="deployment",
        )
        task, needs = routed["task"], routed["required_files"]
        ok, missing = _has_required_files(pipeline_files, needs)
        if not ok:
            await manager.send_agent_response("Deployment Agent", routed["assistant_message"], session_id)
            return

        await manager.send_agent_response("Deployment Agent", routed["assistant_message"], session_id)

        # ── Fetch prior-agent context ─────────────────────────────────────────
        session_context = await _build_session_context(session_id)
        if session_context:
            await manager.send_agent_response("Deployment Agent", "📋 Loaded prior SDLC context.", session_id)
        # Phase 9.3 — fetch raw testing artifacts dict for the gate (separate
        # from the prompt-string built above). WS path is the standalone
        # Deployment Agent UI — never orchestrator-driven, so gate skips.
        ws_artifacts = await _fetch_session_artifacts_safe(session_id)
        ws_testing_artifacts = ws_artifacts.get("testing_artifacts") or {}
        ws_pipeline_context = parse_pipeline_context(pipeline_context)
        ws_requirements_payload = (
            ws_artifacts.get("requirements_payload")
            or ws_pipeline_context.get("requirements")
            or {}
        )
        ws_development_artifacts = (
            ws_artifacts.get("development_artifacts")
            or ws_pipeline_context.get("development")
            or {}
        )

        # ── Build initial pipeline state ──────────────────────────────────────
        pipeline_state: Dict[str, Any] = {
            "messages": [],
            "codebase_extracted": False,
            "testing_extracted": False,
            "codebase_content": "",
            "testing_content": "",
            "combined_content": "",
            "risk_report": "",
            "deploy_report": "",
            "dockerfile_content": "",
            "readme_content": "",
            "docker_instructions": "",
            "errors": [],
            "input_directory": input_dir,
            "output_directory": output_dir,
            "codebase_zip_path": pipeline_files["codebase_zip"],
            "testing_zip_path": pipeline_files["testing_zip"],
            "session_id": session_id,
            "session_context": session_context,
            "deployment_request": dep_req,
            # CI provider chosen upstream (deploy/prepare detection or user override).
            # Empty means Azure Pipelines — see trigger_deployment_pipeline.
            "deploy_via": dep_req.get("deploy_via", "") if isinstance(dep_req, dict) else "",
            "gha_owner": dep_req.get("gha_owner", "") if isinstance(dep_req, dict) else "",
            "gha_repo": dep_req.get("gha_repo", "") if isinstance(dep_req, dict) else "",
            "deployment_plan": "",
            "docker_compose": "",
            "cicd_pipeline_azure": "",
            "cicd_pipeline_github": "",
            "rollback_plan": "",
            "deployment_decision": "",
            "ado_project": "",
            "ado_repo": "",
            "ado_branch": "",
            "ado_repo_url": "",
            "ado_clone_dir": "",
            "tech_stack": "",
            "pipeline_run_id": "",
            "pipeline_run_url": "",
            "push_succeeded": False,
            # Phase 9.3 — gate inputs. WS endpoint = standalone UI, never
            # orchestrator-driven; gate skips on this path.
            "orchestrator_driven": False,
            "testing_artifacts": ws_testing_artifacts,
            "requirements_payload": ws_requirements_payload,
            "development_artifacts": ws_development_artifacts,
            "board_provider": ws_pipeline_context.get("board_provider") or ws_requirements_payload.get("provider_kind") or "azure_devops",
            "pipeline_context": ws_pipeline_context,
            "orchestration_message": orchestration_message,
            # P3.6 B6 — BYOK: tenant from the WS JWT claims, optional model from the message.
            "tenant_id": tenant_id,
            "model_id": message.get("model_id"),
        }

        # ── Execute pipeline ──────────────────────────────────────────────────
        # Phase 9.3 — short-circuit when an early step sets status=blocked
        # (e.g., verify_testing_gate). Subsequent steps would do harm
        # (clone, push, trigger) and confuse the user. The gate's reason
        # is rendered by the structured summary (Phase 9.5).
        # MCP: bind this project's deployment-stage MCP servers for the run so the step
        # tools (deployer.py binds get_mcp_tools()) can call them. No-op when the stage
        # has no MCP assigned or MCP is disabled.
        from shared.services.mcp_injection import mcp_tools_scope, project_stage_server_ids  # noqa: PLC0415
        _dep_pid = ws_pipeline_context.get("project_id")
        _dep_mcp_ids = await project_stage_server_ids(tenant_id or None, _dep_pid, "deployment")
        async with mcp_tools_scope(
            tenant_id or None, _dep_mcp_ids, "deployment",
            project_id=_dep_pid, owner_id=str(user_id) or None,
        ):
            for step_tool in _build_step_plan(task):
                if pipeline_state.get("status") == "blocked":
                    break
                if pipeline_state.get("push_status") == "failed":
                    break
                step_name = step_tool.name.replace("_", " ").title()
                await manager.send_agent_response("Deployment Agent", f"🔄 {step_name}...", session_id)
                try:
                    result = await step_tool.ainvoke({"state": pipeline_state})
                    if isinstance(result, dict):
                        pipeline_state.update(result)
                except Exception as exc:
                    err = classify_error(exc, step_name)
                    pipeline_state.setdefault("errors", []).append(err)
                    await manager.send_agent_response("Deployment Agent", f"❌ {err}", session_id)

        # ── Persist artifacts ─────────────────────────────────────────────────
        handoff_sentinel = await _persist_deployment_artifacts(session_id, pipeline_state)
        summary = _pipeline_summary(pipeline_state)

        prefix = "🎉" if handoff_sentinel else "⚠️"
        final_message = f"{prefix} {summary}"
        await manager.send_agent_response("Deployment Agent", final_message, session_id)
        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid.uuid4()),
                "type": "complete",
                "session_id": session_id,
                "message": "Deployment pipeline completed",
                "time": "just now",
            },
        })

    except Exception as exc:
        err = classify_error(exc, "deployment pipeline")
        await manager.send_agent_response("Deployment Agent", f"❌ {err}", session_id)
        logger.error("Critical error in deployment pipeline: %s", exc)


# ── REST endpoint ─────────────────────────────────────────────────────────────

@deployment_router_orchestrator.post("/chat/")
async def chat(
    session_id: str = Form(...),
    user_message: str = Form(None),
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    provider_kind: str = Form(None),
    user_id: str = Form(...),
    deployment_request: str = Form(None),
    uploaded_files: List[UploadFile] = File(default_factory=list),
):
    try:
        set_session_id(session_id)
        set_user_id(user_id)
        set_provider_kind(provider_kind or "azure_devops")
        set_websocket_context(manager, session_id)

        input_dir = os.path.join(esett.FILES, user_id, "orchestrator", session_id, "input")
        output_dir = os.path.join(esett.FILES, user_id, "orchestrator", session_id, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        pipeline_files: Dict[str, Optional[str]] = {"codebase_zip": None, "testing_zip": None}
        for uf in uploaded_files:
            save_path = os.path.join(input_dir, uf.filename)
            async with aiofiles.open(save_path, "wb") as f:
                await f.write(await uf.read())
            lower = uf.filename.lower()
            if any(kw in lower for kw in ("codebase", "source", "src")):
                pipeline_files["codebase_zip"] = save_path
            elif any(kw in lower for kw in ("test", "report")):
                pipeline_files["testing_zip"] = save_path

        dep_req = json.loads(deployment_request) if deployment_request else {}
        orchestration_message = build_agent_input_text(
            conversation_context=conversation_context,
            task_intent=task_intent,
            pipeline_context=pipeline_context,
            pipeline_sections=("requirements", "development", "testing"),
        )
        effective_user_message = user_message or task_intent or orchestration_message
        routed = _route_task(effective_user_message or "", pipeline_files)
        task, needs = routed["task"], routed["required_files"]
        ok, missing = _has_required_files(pipeline_files, needs)
        if not ok:
            return {"conversation_id": session_id, "responses": routed["assistant_message"], "output_filename": ""}

        session_context = await _build_session_context(session_id)
        # Phase 9.3 — also pull raw artifacts dict for the testing gate.
        rest_artifacts = await _fetch_session_artifacts_safe(session_id)
        rest_testing_artifacts = rest_artifacts.get("testing_artifacts") or {}
        rest_pipeline_context = parse_pipeline_context(pipeline_context)
        rest_requirements_payload = (
            rest_artifacts.get("requirements_payload")
            or rest_pipeline_context.get("requirements")
            or {}
        )
        rest_development_artifacts = (
            rest_artifacts.get("development_artifacts")
            or rest_pipeline_context.get("development")
            or {}
        )
        # Orchestrator-driven if and only if conversation_context form param
        # was supplied (orchestrator wraps the user's intent into it; standalone
        # REST callers send only user_message + uploaded_files).
        rest_orchestrator_driven = bool(conversation_context)

        pipeline_state: Dict[str, Any] = {
            "messages": [],
            "codebase_extracted": False,
            "testing_extracted": False,
            "codebase_content": "",
            "testing_content": "",
            "combined_content": "",
            "risk_report": "",
            "deploy_report": "",
            "dockerfile_content": "",
            "readme_content": "",
            "docker_instructions": "",
            "errors": [],
            "input_directory": input_dir,
            "output_directory": output_dir,
            "codebase_zip_path": pipeline_files["codebase_zip"],
            "testing_zip_path": pipeline_files["testing_zip"],
            "session_id": session_id,
            "session_context": session_context,
            "deployment_request": dep_req,
            # CI provider chosen upstream (deploy/prepare detection or user override).
            # Empty means Azure Pipelines — see trigger_deployment_pipeline.
            "deploy_via": dep_req.get("deploy_via", "") if isinstance(dep_req, dict) else "",
            "gha_owner": dep_req.get("gha_owner", "") if isinstance(dep_req, dict) else "",
            "gha_repo": dep_req.get("gha_repo", "") if isinstance(dep_req, dict) else "",
            "deployment_plan": "",
            "docker_compose": "",
            "cicd_pipeline_azure": "",
            "cicd_pipeline_github": "",
            "rollback_plan": "",
            "deployment_decision": "",
            "ado_project": "",
            "ado_repo": "",
            "ado_branch": "",
            "ado_repo_url": "",
            "ado_clone_dir": "",
            "tech_stack": "",
            "pipeline_run_id": "",
            "pipeline_run_url": "",
            "push_succeeded": False,
            # Phase 9.3 — gate inputs.
            "orchestrator_driven": rest_orchestrator_driven,
            "testing_artifacts": rest_testing_artifacts,
            "requirements_payload": rest_requirements_payload,
            "development_artifacts": rest_development_artifacts,
            "board_provider": rest_pipeline_context.get("board_provider") or rest_requirements_payload.get("provider_kind") or "azure_devops",
            "pipeline_context": rest_pipeline_context,
            "orchestration_message": orchestration_message,
            # P3.6 B6 — BYOK: the REST/orchestrator path does not yet forward a
            # tenant_id form field (the orchestrator's _agent_form_data omits it,
            # same as the testing/design REST endpoints). Fail CLOSED with an
            # empty tenant so generation raises NoModelConfiguredError rather than
            # ever using a platform key. Wiring tenant_id through the orchestrator
            # form is a separate, cross-agent change (see report concern).
            "tenant_id": "",
            "model_id": None,
        }

        for step_tool in _build_step_plan(task):
            if pipeline_state.get("status") == "blocked":
                break  # Phase 9.3 — gate failure short-circuits rest of pipeline
            if pipeline_state.get("push_status") == "failed":
                break
            try:
                result = await step_tool.ainvoke({"state": pipeline_state})
                if isinstance(result, dict):
                    pipeline_state.update(result)
            except Exception as exc:
                pipeline_state.setdefault("errors", []).append(classify_error(exc, step_tool.name))

        handoff_sentinel = await _persist_deployment_artifacts(session_id, pipeline_state)
        summary = _pipeline_summary(pipeline_state)
        output_file = os.path.join(output_dir, "deployment_summary.txt")
        async with aiofiles.open(output_file, "w", encoding="utf-8") as f:
            await f.write(summary)

        responses_body = summary
        return {
            "conversation_id": session_id,
            "deployment_decision": pipeline_state.get("deployment_decision", ""),
            "pipeline_run_url": pipeline_state.get("pipeline_run_url", ""),
            "pipeline_run_id": pipeline_state.get("pipeline_run_id", ""),
            "ado_project": pipeline_state.get("ado_project", ""),
            "ado_repo": pipeline_state.get("ado_repo", ""),
            "ado_branch": pipeline_state.get("ado_branch", ""),
            "responses": responses_body,
            "output_filename": output_file,
        }

    except Exception as exc:
        err = classify_error(exc, "deployment REST endpoint")
        logger.error("REST error: %s", exc)
        return {"conversation_id": session_id, "responses": err, "output_filename": ""}


# ── Utility endpoints ─────────────────────────────────────────────────────────

@deployment_router_orchestrator.get("/download/{filename}")
async def download_file(filename: str, session_id: str, user_id: str):
    set_agent_folder("orchestrator")
    base_path = os.path.abspath(os.path.join(esett.FILES, user_id, "orchestrator", session_id))
    for candidate in [
        os.path.join(base_path, "output", filename),
        os.path.join(base_path, filename),
    ]:
        if os.path.isfile(candidate):
            if os.path.commonpath([base_path, candidate]) != base_path:
                raise HTTPException(status_code=403, detail="Unauthorized access")
            return FileResponse(candidate, filename=filename)
    raise HTTPException(status_code=404, detail="File not found")


@deployment_router_orchestrator.get("/sessions")
async def sessions_info():
    return {
        "supported_files": ["codebase.zip", "testing_reports.zip"],
        "generated_outputs": [
            "deployment_plan.md",
            "risk_analysis_report.md",
            "deployment_validation_report.md",
            "Dockerfile",
            "docker-compose.yml",
            "azure-pipelines.yml",
            ".github/workflows/deploy.yml",
            "README.md",
            "docker_build_instructions.md",
            "rollback_plan.md",
        ],
        "supported_commands": [
            "deploy",
            "trigger pipeline",
            "deploy from azure",
            "create deployment files",
            "dockerfile only",
            "readme only",
            "risk analysis",
            "deployment validation",
            "docker instructions",
        ],
        "modes": {
            "ado_deploy": "Clone ADO repo → generate Dockerfile + azure-pipelines.yml → push → trigger pipeline. Triggered by 'deploy' / 'trigger pipeline' (no zips needed).",
            "full_pipeline": "Generate all 10 deployment artifacts from uploaded codebase + testing zips.",
        },
    }


@deployment_router_orchestrator.get("/status/{session_id}")
async def status_check(session_id: str, user_id: str):
    set_agent_folder("orchestrator")
    base = os.path.join(esett.FILES, user_id, "orchestrator", session_id, "output")
    filenames = [
        "deployment_plan.md", "risk_analysis_report.md", "deployment_validation_report.md",
        "Dockerfile", "docker-compose.yml", "azure-pipelines.yml",
        "README.md", "docker_build_instructions.md", "rollback_plan.md",
    ]
    files = {}
    for fname in filenames:
        path = os.path.join(base, fname)
        exists = os.path.isfile(path)
        files[fname] = {"exists": exists, "size": os.path.getsize(path) if exists else 0, "path": path}
    completed = sum(1 for f in files.values() if f["exists"])
    total = len(files)
    return {
        "session_id": session_id,
        "files": files,
        "analysis_complete": completed == total,
        "completed_files": completed,
        "total_files": total,
        "completion_percentage": round(completed / total * 100, 1) if total else 0,
    }
