"""ADO pipeline trigger + poll node — Phase B.2.

When a chat message matches "run pipeline N on branch X" or a structured
pipeline_target form param is provided, this node:
  1. POSTs to ADO pipelines/{id}/runs to trigger the run
  2. Polls pipelines/{id}/runs/{run_id} with backoff up to 30 minutes
  3. Stores a PipelineRun record into state["pipeline_run"] which
     _build_testing_artifact rolls into TestingArtifact.pipeline_run.

Phase B.3 extends this node to also enumerate + download published artifacts
once the run reaches a terminal state.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from shared.models.testing import PipelineRun

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, logger


# Backoff schedule (seconds). Sum ≈ 1800s (30 min).
_BACKOFF = (5, 10, 20, 30, 30, 60, 60, 60, 90, 120, 120, 180, 180, 180, 180, 180, 180)
_TERMINAL_STATES = {"completed", "cancelling"}


async def _default_project_from_connector() -> str:
    """If the ADO connector exposes exactly one project, return its name.
    Otherwise return '' so callers can prompt the user to disambiguate.
    Best-effort; any error returns ''."""
    try:
        from config.connector_client import get_connector
        creds = get_connector("azure_devops")
    except Exception:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10, auth=("", creds["pat"])) as client:
            url = f"{creds['org_url'].rstrip('/')}/_apis/projects?api-version=7.1&$top=10"
            r = await client.get(url)
            r.raise_for_status()
            projects = r.json().get("value", [])
        if len(projects) == 1:
            return projects[0].get("name") or ""
    except Exception:
        return ""
    return ""

_CHAT_PATTERN = re.compile(
    r"run\s+pipeline\s+(\d+)"
    r"(?:\s+(?:in|of|for)\s+project\s+([\w.\-]+))?"
    r"(?:\s+on\s+branch\s+([\w/.\-]+))?"
    r"(?:\s+(?:in|of|for)\s+project\s+([\w.\-]+))?",  # accept "in project X" before OR after "on branch Y"
    re.IGNORECASE,
)


def parse_pipeline_request(user_message: str) -> Optional[dict]:
    """Extract {pipeline_id, branch, project?} from a natural-language chat message.

    Examples:
      "run pipeline 42"                                    -> id=42, branch=main, project=None
      "run pipeline 42 on branch main"                     -> id=42, branch=main, project=None
      "run pipeline 42 in project aicore on branch main"   -> id=42, branch=main, project=aicore
      "run pipeline 42 on branch main in project aicore"   -> id=42, branch=main, project=aicore
    """
    if not user_message:
        return None
    m = _CHAT_PATTERN.search(user_message)
    if not m:
        return None
    pid = int(m.group(1))
    project = m.group(2) or m.group(4)
    branch = m.group(3)
    out = {"pipeline_id": pid, "branch": branch or "main"}
    if project:
        out["project"] = project
    return out


async def trigger_and_poll(state: SuperAgentState):
    """Trigger pipeline + poll until terminal state. Persists PipelineRun on state."""
    target = state.get("pipeline_target")
    if not isinstance(target, dict):
        # Try to parse from chat message — graph may also reach here via the
        # "trigger_pipeline_and_collect" intent without a structured form param.
        prompt = state.get("user_prompt") or ""
        parsed = parse_pipeline_request(prompt)
        if not parsed:
            blog("Pipeline target missing; skipping pipeline node", level="WARNING")
            return {}
        target = parsed

    project = (
        target.get("project")
        or (state.get("clone_target") or {}).get("project")
        or ""
    )
    pipeline_id = target.get("pipeline_id")
    branch = target.get("branch") or "main"

    # Bug-fix: when the user omits project (e.g. "run pipeline 42 on branch main"),
    # try to default it from the ADO connector. If the connector has exactly one
    # project we pick it; otherwise we ask the user (skip with a clear log).
    if not project:
        project = await _default_project_from_connector()
        if project:
            blog(f"pipeline project not specified — defaulting to {project!r}")

    if not pipeline_id:
        blog("pipeline_target missing pipeline_id — skipping", level="WARNING")
        return {}
    if not project:
        blog(
            "pipeline project not specified and connector has more than one (or none) — "
            "rephrase as 'run pipeline N in project X on branch Y'",
            level="WARNING",
        )
        return {}

    # Lazy imports — keep this node optional in unit-test envs.
    try:
        from config.connector_client import get_connector, ConnectorNotInstalledError
    except ImportError as exc:
        logger.warning(f"B.2: connector_client unavailable ({exc}) — skipping pipeline node")
        return {}

    # Post-MVP Phase 1 — narrow from `except Exception` to the specific connector
    # error so real bugs (network, auth) propagate as 500 instead of being
    # silently swallowed. Friendly message for the connector-missing case.
    try:
        creds = get_connector("azure_devops")
    except ConnectorNotInstalledError as exc:
        logger.error(f"B.2: ADO connector not installed ({exc}) — cannot trigger pipeline")
        blog(
            f"Cannot trigger pipeline — **Azure DevOps connector is not configured**.\n\n"
            f"Install it on the Connectors page or set ADO_ORG_URL + ADO_PAT in `.env` "
            f"and restart FastAPI.\n\nUnderlying error: {exc}",
            level="ERROR",
        )
        return {}

    org_url = creds["org_url"].rstrip("/")
    pat = creds["pat"]

    blog(f"Triggering ADO pipeline {pipeline_id} on branch {branch}...")
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=30, auth=("", pat)) as client:
            trigger_url = f"{org_url}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1"
            body = {"resources": {"repositories": {"self": {"refName": f"refs/heads/{branch}"}}}}
            r = await client.post(trigger_url, json=body)
            r.raise_for_status()
            data = r.json()
            run_id = data.get("id")
            web_url = data.get("_links", {}).get("web", {}).get("href", "")
    except Exception as exc:
        logger.error(f"B.2: pipeline trigger failed: {exc}")
        blog(f"Pipeline trigger failed: {exc}", level="ERROR")
        return {}

    if not run_id:
        blog("Pipeline trigger returned no run_id", level="ERROR")
        return {}

    logger.info(f"B.2: triggered pipeline {pipeline_id} → run_id={run_id}")
    blog(f"Pipeline run {run_id} triggered, polling for completion...")

    # Poll loop with backoff capped at ~30 min total.
    final_state: str = "inProgress"
    final_result: Optional[str] = None
    finished_at: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=30, auth=("", pat)) as client:
            for delay in _BACKOFF:
                await asyncio.sleep(delay)
                status_url = (
                    f"{org_url}/{project}/_apis/pipelines/{pipeline_id}/runs/{run_id}"
                    f"?api-version=7.1"
                )
                try:
                    r = await client.get(status_url)
                    r.raise_for_status()
                    data = r.json()
                except Exception as exc:
                    logger.warning(f"B.2: poll error (continuing): {exc}")
                    continue
                state_now = data.get("state") or ""
                result_now = data.get("result")
                logger.info(f"B.2: run {run_id} state={state_now} result={result_now}")
                if state_now in _TERMINAL_STATES:
                    final_state = state_now
                    final_result = result_now
                    finished_at = datetime.now(timezone.utc).isoformat()
                    break
            else:
                # Loop exhausted without terminal state
                final_state = "timeout"
                blog(f"Pipeline run {run_id} did not finish within 30 min — marking as timeout", level="WARNING")
    except Exception as exc:
        logger.error(f"B.2: poll loop crashed: {exc}")
        final_state = "timeout"

    pipeline_run = PipelineRun(
        run_id=run_id,
        pipeline_id=pipeline_id,
        project=project,
        state=final_state,
        result=final_result,
        started_at=started_at,
        finished_at=finished_at,
        web_url=web_url,
    )
    blog(f"Pipeline run finished — state={final_state} result={final_result or 'n/a'}")

    # Phase B.3 — download published artifacts (Trivy SARIF / Sonar JSON) once
    # the run has reached a terminal state. Failures are logged but never
    # raise — the pipeline_run record is still returned.
    findings_dicts: list = []
    if final_state == "completed":
        try:
            findings = await _ingest_security_artifacts(
                org_url, project, run_id, pat
            )
            findings_dicts = [f.model_dump() for f in findings]
            if findings_dicts:
                blog(f"Ingested {len(findings_dicts)} security findings from pipeline artifacts")
        except Exception as exc:
            logger.warning(f"B.3: security-artifact ingestion failed: {exc}")

    return {
        "pipeline_run": pipeline_run.model_dump(),
        "security_findings": findings_dicts,
    }


# ── Phase B.3 — pipeline-artifact ingestion ─────────────────────────────────

async def _ingest_security_artifacts(
    org_url: str,
    project: str,
    build_id: int,
    pat: str,
) -> list:
    """Enumerate ADO build artifacts; download those whose name matches
    `trivy*` / `sonar*`; unzip into a tmpdir; run the Trivy/Sonar parsers."""
    import tempfile
    import zipfile

    import httpx

    from agents_orchestrator.testing_agent.tools.security_parsers import parse_artifacts_dir

    list_url = (
        f"{org_url}/{project}/_apis/build/builds/{build_id}/artifacts"
        f"?api-version=7.1"
    )
    extract_root = tempfile.mkdtemp(prefix="testing_agent_artifacts_")
    findings = []

    async with httpx.AsyncClient(timeout=120, auth=("", pat)) as client:
        try:
            r = await client.get(list_url)
            r.raise_for_status()
            artifacts = r.json().get("value", [])
        except httpx.HTTPStatusError as exc:
            # 404 = pipeline run produced no published artifacts. Not an error.
            if exc.response.status_code == 404:
                logger.info(f"B.3: no artifacts published for build {build_id}")
                return findings
            raise

        for art in artifacts:
            name = (art.get("name") or "").lower()
            if not any(needle in name for needle in ("trivy", "sonar", "security", "sarif")):
                continue
            download_url = (art.get("resource") or {}).get("downloadUrl")
            if not download_url:
                continue
            logger.info(f"B.3: downloading artifact {art.get('name')!r}")
            try:
                resp = await client.get(download_url)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning(f"B.3: download failed for {art.get('name')!r}: {exc}")
                continue

            zip_path = os.path.join(extract_root, f"{art.get('name', 'artifact')}.zip")
            with open(zip_path, "wb") as f:
                f.write(resp.content)
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    sub_extract = os.path.join(extract_root, art.get("name", "artifact"))
                    zf.extractall(sub_extract)
            except zipfile.BadZipFile:
                logger.warning(f"B.3: artifact {art.get('name')!r} is not a zip; skipping")
                continue

    findings = parse_artifacts_dir(extract_root)
    return findings
