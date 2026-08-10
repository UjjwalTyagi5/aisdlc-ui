"""Temporal activity wrapper for the security agent.

Follows the same pattern as code_review_activity.py:
  - Idempotency check via check_existing_artifact
  - Lazy import of the LangGraph graph
  - Connector injection for tenant context
  - Audit callback handler
  - Clarification detection
  - write_and_notify for persistence
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from langchain_core.messages import HumanMessage
from temporalio import activity

from shared.models.artifacts import SecurityArtifact
from shared.models.workflow_models import ClarificationRequest, SDLCWorkflowInput
from shared.services.metrics import TEMPORAL_ACTIVITY_DURATION
from workflows.activities._base import (
    check_existing_artifact,
    detect_clarification_need,
    mcp_tools_for_stage,
    stage_connector_kind,
    write_and_notify,
)
from workflows.activities.pipeline_session import _read_run_upstream, pipeline_session

logger = logging.getLogger(__name__)

_ACTIVITY_NAME = "run_security_activity"


def _get_scan_session(run_id):
    """The Security agent's own per-session state getter, keyed by session id (=run_id)."""
    from agents_orchestrator.security_agent.config.session_state import get_session  # noqa: PLC0415

    return get_session(str(run_id))


def _capture_security_artifact(run_id, final_state) -> dict:
    """Prefer the structured `submit_security_review` output; fall back to the summary.

    The rich standalone artifact (`shared.models.security.SecurityArtifact`, stored in
    the scan session's `last_artifact` by `submit_security_review`) carries
    findings/risk_score/signoff/sbom. Return it verbatim when present. Otherwise fall
    back to the final-message summary as a persistence `SecurityArtifact` dict —
    identical to the pre-Task-8 behaviour.
    """
    s = _get_scan_session(run_id)
    last_artifact = getattr(s, "last_artifact", None)
    if last_artifact:
        return last_artifact

    messages = (final_state or {}).get("messages", [])
    scan_summary: Optional[str] = None
    if messages:
        last = messages[-1]
        scan_summary = getattr(last, "content", None)
        if isinstance(scan_summary, list):
            scan_summary = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in scan_summary
            )
    return SecurityArtifact(
        scan_summary=scan_summary if isinstance(scan_summary, str) else None
    ).model_dump()


def _to_persistence_artifact(art: dict, version: int) -> SecurityArtifact:
    """Map a captured artifact dict onto the persistence `SecurityArtifact` for the
    Temporal return contract. Handles both the rich `submit_security_review` shape
    (summary/risk_score/findings) and the plain fallback (scan_summary). The durable
    record persisted by `write_and_notify` is the full captured dict; this is only the
    typed return value the workflow receives."""
    findings = art.get("findings") or []
    return SecurityArtifact(
        risk_score=art.get("risk_score"),
        scan_summary=art.get("scan_summary") or art.get("summary"),
        dependency_findings=[f for f in findings if isinstance(f, dict) and f.get("category") == "sca"],
        code_findings=[f for f in findings if isinstance(f, dict) and f.get("category") == "sast"],
        secret_findings=[f for f in findings if isinstance(f, dict) and f.get("category") == "secret"],
        security_sign_off=(((art.get("signoff") or {}).get("decision")) == "pass"),
        version=art.get("version") or version,
    )


async def _read_run_upstream_for_security(input: SDLCWorkflowInput) -> Optional[dict]:
    """Return this run's `development_artifacts` dict (the repo to scan), or None."""
    try:
        upstream = await _read_run_upstream(str(input.run_id))
    except Exception as exc:  # never break the activity on a read
        logger.warning("_read_run_upstream_for_security(%s) failed: %s", input.run_id, exc)
        return None
    dev = upstream.get("development_artifacts")
    return dev if isinstance(dev, dict) else None


def _repo_ref_from_dev(dev: Optional[dict]) -> Optional[dict]:
    """Build a Bridge repo_ref from development_artifacts, or None if no repo url.

    Security scans the whole checkout — no diff base is needed, so `base` is omitted.
    """
    if not dev or not dev.get("repo_url"):
        return None
    return {
        "repo_url": dev.get("repo_url"),
        "ref": dev.get("branch_name"),
    }


@activity.defn(name=_ACTIVITY_NAME)
async def run_security_activity(input: SDLCWorkflowInput) -> ClarificationRequest | SecurityArtifact:
    """Idempotent security agent Temporal activity."""
    start = time.monotonic()
    status = "ok"
    try:
        existing = await check_existing_artifact(
            input.run_id, "security", input.agent_version, tenant_id=input.tenant_id
        )
        if existing is not None:
            return _to_persistence_artifact(existing, input.agent_version)

        from agents_orchestrator.security_agent.agents.scanner import (  # noqa: PLC0415
            app as _security_graph,
        )

        from config.connectors.context import set_connector, clear_connector  # noqa: PLC0415
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415

        _connector_injected = False
        if input.tenant_id:
            try:
                set_connector(
                    await get_connector_for_session(
                        kind=stage_connector_kind(input, "security"),
                        tenant_id=input.tenant_id,
                    )
                )
                _connector_injected = True
            except Exception:
                _connector_injected = False

        from contextlib import ExitStack  # noqa: PLC0415

        from shared.observability import build_agent_callbacks  # noqa: PLC0415

        _callbacks, _trace_cm = build_agent_callbacks(
            run_id=str(input.run_id),
            tenant_id=input.tenant_id or "",
            agent_type="security",
            model=input.model_id,
            offering_id=input.offering_id,
            project_id=input.project_id,
        )
        _trace_stack = ExitStack()
        _trace_stack.enter_context(_trace_cm)
        _cfg = {
            "configurable": {"thread_id": str(input.run_id)},
            "recursion_limit": 100,
            "callbacks": _callbacks,
        }

        # Resolve THIS run's dev repo pointer; clone the whole checkout via the Bridge
        # when present (security scans the full tree — no diff base needed).
        dev = await _read_run_upstream_for_security(input)
        repo_ref = _repo_ref_from_dev(dev)

        async with pipeline_session(
            input, "security", needs_repo=bool(repo_ref), repo_ref=repo_ref
        ) as ps:
            # Seed the scan session with the run-scoped workspace so the scanner tools
            # (scan_dependencies / scan_code / scan_secrets / read_repo_file) resolve
            # the correct clone.
            if getattr(ps, "_workspace", None):
                s = _get_scan_session(input.run_id)
                s.work_dir = ps._workspace.work_dir
                s.tenant_id = input.tenant_id or ""
                s.project_id = str(input.project_id)

            try:
                async with mcp_tools_for_stage(input, "security"):
                    if input.clarification_answer:
                        final_state = await _security_graph.ainvoke(
                            {"messages": [HumanMessage(content=input.clarification_answer)]},
                            config=_cfg,
                        )
                    else:
                        prompt = (
                            f"Perform security analysis for project_id={input.project_id}. "
                            f"trigger={input.trigger}"
                            + (f", work_item_id={input.work_item_id}" if input.work_item_id else "")
                            + ". Run all available security scanners (Trivy, Semgrep SAST, Gitleaks) and produce your security findings."
                        )
                        final_state = await _security_graph.ainvoke(
                            {"messages": [HumanMessage(content=prompt)], "tenant_id": input.tenant_id, "model_id": input.model_id, "offering_id": input.offering_id},
                            config=_cfg,
                        )
            finally:
                _trace_stack.close()
                if _connector_injected:
                    clear_connector()

            clarification = detect_clarification_need(final_state, str(input.run_id), "security")
            if clarification is not None:
                return clarification

            # Prefer the structured submit_security_review output; else summary fallback.
            art = _capture_security_artifact(input.run_id, final_state)
            art.setdefault("version", input.agent_version)

            await write_and_notify(input.run_id, "security", art, tenant_id=input.tenant_id)
            return _to_persistence_artifact(art, input.agent_version)

    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        TEMPORAL_ACTIVITY_DURATION.labels(
            activity=_ACTIVITY_NAME, status=status
        ).observe(elapsed)
