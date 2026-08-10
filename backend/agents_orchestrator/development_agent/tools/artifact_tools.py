"""Artifact tools — fetch upstream agent outputs and submit development artifacts."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from langchain_core.tools import tool

from agents_orchestrator.development_agent.config.session_state import get_session
from config.ws_helper import get_session_id
from shared.services.agent_session_store import fetch_session_artifacts, patch_session_artifacts

logger = logging.getLogger("development_agent")


@tool
async def read_design_artifact() -> str:
    """Fetch the requirements payload and design artifacts produced by the
    Requirements and Design agents for the current session.

    Call this as the very first action every session (Phase 0).
    No arguments needed — the session ID is resolved automatically.

    Returns a JSON string with keys:
      - requirements_payload: structured requirements from the Requirements Agent
      - design_artifacts: architecture document from the Design Agent
      - current_stage: last recorded pipeline stage
    """
    session_id = get_session_id()
    try:
        data = await fetch_session_artifacts(session_id, tenant_id=None) or {}
        if not data:
            return json.dumps({"error": f"No AgentSession found for session_id={session_id}. Design phase may not have run yet."})
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
async def submit_development_artifacts(
    batch_id: str = "TEST-READY",
    work_item_ids: Optional[List[str]] = None,
) -> str:
    """Finalise the development session: persist all accumulated artifacts and
    emit the pipeline handoff to Testing.

    **Call this when development is complete.** Completion can mean any of:
    - PR has been created via `create_pr` (canonical happy path), OR
    - Branch has been pushed to the remote and the user said "no PR" /
      "push but don't create PR" / equivalent (push-only completion), OR
    - Local commits are recorded and the user signaled "we're done" / "test it"
      / "hand off to testing" without explicit PR step.

    The downstream Testing Agent reads `development_artifacts` from the session
    store to auto-clone the branch you just pushed. If you skip this tool, Testing has
    no context — it will ask the user "which branch?" instead of testing the
    one you just pushed. Always call this as the last step regardless of
    whether a PR was opened.

    Args:
        batch_id: Identifier for this development batch (default "TEST-READY").
        work_item_ids: ADO/Jira work item IDs linked to this change, if not
                       already recorded by create_pr.
    """
    session_id = get_session_id()
    s = get_session(session_id)
    arts = s.dev_artifacts

    # Sync any session-level fields that tools may have set after artifact init
    if s.repo_url and not arts.repo_url:
        arts.repo_url = s.repo_url
    if s.repo_type and not arts.repo_type:
        arts.repo_type = s.repo_type  # type: ignore[assignment]
    if s.branch_name and not arts.branch_name:
        arts.branch_name = s.branch_name
    if s.pr_url and not arts.pr_url:
        arts.pr_url = s.pr_url
    if s.pr_title and not arts.pr_title:
        arts.pr_title = s.pr_title
    if work_item_ids and not arts.work_item_ids:
        arts.work_item_ids = [str(w) for w in work_item_ids]

    # Determine final status
    if arts.pr_url:
        arts.status = "pr_created"
    elif arts.lint_results or arts.test_results or arts.build_results:
        arts.status = "validated"
    elif arts.changed_files or arts.generated_files:
        arts.status = "in_progress"

    handoff_payload = {
        "to": "testing",
        "batch_id": batch_id,
        "stage_completed": "development",
        "context_keys": [
            "requirements_payload",
            "design_artifacts",
            "development_artifacts",
        ],
        "triggered_by": "user_confirmed",
    }

    # Persist to Postgres via in-process store
    try:
        await patch_session_artifacts(
            session_id,
            {"development_artifacts": arts.model_dump(), "last_handoff_event": handoff_payload},
            tenant_id=None,
        )
        logger.info("Development artifacts persisted for session %s", session_id)
    except Exception as exc:
        logger.warning("Failed to persist development artifacts: %s", exc)

    return (
        "Development artifacts persisted successfully. "
        "Inform the user: implementation is complete and the PR is ready for review."
    )
