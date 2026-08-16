"""Native tools for the Code Review agent (read-only on the repo).

The diff under review is prepared by the API (clone + `git diff`) and injected
into the agent's context; these tools let the agent (1) read surrounding code for
semantic context, (2) pull light cross-file context, (3) read upstream
requirements/design artifacts when the project has them, and (4) submit the
final structured review. None of them mutate the repository.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

from langchain_core.tools import tool

from agents_orchestrator.code_review_agent.config.session_state import get_session
from config.ws_helper import broadcast_log, get_session_id
from config.connection_manager import manager

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", "dist", "build"}
_MAX_FILE_BYTES = 200_000


def _work_dir() -> pathlib.Path | None:
    s = get_session(get_session_id())
    return pathlib.Path(s.work_dir) if s.work_dir else None


@tool
async def read_repo_file(path: str) -> str:
    """Read one repo-relative file from the cloned review workspace for context.

    Args:
        path: repo-relative file path (e.g. "src/auth.py").
    Returns: the file's text (truncated), or an error message.
    """
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no review workspace prepared. Ask the user to select a branch or PR first."
    try:
        target = (root / path).resolve()
        target.relative_to(root.resolve())
    except ValueError:
        return "ERROR: path traversal denied."
    if not target.exists() or not target.is_file():
        return f"ERROR: file not found: {path}"
    data = target.read_bytes()[:_MAX_FILE_BYTES]
    return data.decode("utf-8", errors="replace")


@tool
async def search_repo(query: str, max_results: int = 40) -> str:
    """Grep the cloned repo for a literal string (find callers / importers / usages).

    Args:
        query: literal substring to search for.
        max_results: cap on matching lines returned.
    Returns: JSON list of {file, line, text} matches.
    """
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no review workspace prepared."
    hits: list[dict] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if len(hits) >= max_results:
                break
            fp = pathlib.Path(dirpath) / fn
            try:
                if fp.stat().st_size > _MAX_FILE_BYTES:
                    continue
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        rel = str(fp.relative_to(root)).replace("\\", "/")
                        hits.append({"file": rel, "line": i, "text": line.strip()[:200]})
                        if len(hits) >= max_results:
                            break
            except Exception:
                continue
    return json.dumps({"matches": hits, "count": len(hits)})


async def _latest_artifact_column(tenant_id: str, project_id: str, column: str) -> dict | None:
    from sqlalchemy import select
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    if not tenant_id or not project_id:
        return None
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            col = getattr(Run, column)
            stmt = (
                select(col)
                .where(Run.project_id == uuid.UUID(project_id), col.isnot(None))
                .order_by(Run.created_at.desc())
                .limit(1)
            )
            row = (await db.execute(stmt)).scalars().first()
            return row
    except Exception:
        return None


async def _run_artifact_column(tenant_id: str, run_id: str, column: str) -> dict | None:
    """Read one artifact column off the CURRENT run (pipeline mode).

    Unlike `_latest_artifact_column` (which returns the project-latest row for the
    standalone page), this reads the exact `runs` row the pipeline is executing, so
    Code Review sees THIS run's upstream Requirements/Design — not a stale sibling run.
    """
    from sqlalchemy import select
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    if not tenant_id or not run_id:
        return None
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            col = getattr(Run, column)
            stmt = select(col).where(Run.id == uuid.UUID(str(run_id)))
            row = (await db.execute(stmt)).scalars().first()
            return row
    except Exception:
        return None


async def _run_exists(tenant_id: str, run_id: str) -> bool:
    """True iff a `runs` row with this id exists — the pipeline-mode discriminator.

    A standalone chat session id is a client-generated UUID with no `runs` row; a
    pipeline session id IS the run_id. Any DB/parse failure returns False so we fall
    through to the unchanged standalone path.
    """
    if not tenant_id or not run_id:
        return False
    try:
        _uid = uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError):
        return False
    try:
        from sqlalchemy import select
        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Run

        async with get_db_session_for_tenant(tenant_id) as db:
            hit = (await db.execute(select(Run.id).where(Run.id == _uid))).scalars().first()
            return hit is not None
    except Exception:
        return False


async def _read_artifact_column(s, column: str) -> dict | None:
    """Dual-mode artifact read shared by the upstream-read tools.

    Pipeline mode (session id is a real UUID AND a `runs` row exists) reads the
    CURRENT run. Otherwise falls through to the UNCHANGED standalone project-latest
    path so the live Code Review page behaves byte-for-byte as before.
    """
    sid = get_session_id()
    if await _run_exists(s.tenant_id, sid):
        return await _run_artifact_column(s.tenant_id, sid, column)
    return await _latest_artifact_column(s.tenant_id, s.project_id, column)


@tool
async def read_requirements_payload() -> str:
    """Read this project's latest Requirements payload (acceptance criteria), if any.

    Returns the requirements JSON, or a note that none exists (brownfield review).
    """
    s = get_session(get_session_id())
    art = await _read_artifact_column(s, "requirements_payload")
    if not art:
        return "No requirements artifact found for this project (brownfield review — judge the diff on its own merits)."
    return json.dumps(art)[:12000]


@tool
async def read_design_artifacts() -> str:
    """Read this project's latest Design artifacts (API contracts / schema / ADRs), if any.

    Returns the design JSON, or a note that none exists.
    """
    s = get_session(get_session_id())
    art = await _read_artifact_column(s, "design_artifacts")
    if not art:
        return "No design artifact found for this project (review without design-conformance checks)."
    return json.dumps(art)[:12000]


@tool
async def submit_code_review(review_json: str) -> str:
    """Submit the final structured code review. Call this ONCE when analysis is done.

    Args:
        review_json: a JSON object with keys:
          summary (markdown), merge_recommendation
          ("approve"|"request_changes"|"needs_discussion"),
          findings: [{id, severity, category, file, line, description, recommendation, autofix_patch?}],
          requirements_coverage: [{ac_id, status, note}],
          design_conformance: [{rule, status, note}],
          metrics: {complexity_delta?, dupe_delta?, debt_delta?}
    Returns a confirmation string.
    """
    from shared.models.code_review import (
        CodeReviewArtifact, ReviewContext, ReviewMetrics,
        ReviewFinding, CoverageEntry, ConformanceEntry,
    )

    s = get_session(get_session_id())
    try:
        payload = json.loads(review_json) if isinstance(review_json, str) else dict(review_json)
    except Exception as exc:
        return f"ERROR: review_json was not valid JSON ({exc}). Re-send a single JSON object."

    ctx = ReviewContext(
        repo_name=s.repo_name, ado_project=s.ado_project, mode=s.mode or "branch",
        source_branch=s.source_branch, base_branch=s.base_branch,
        pr_id=s.pr_id or None, pr_title=s.pr_title or None,
        head_sha=s.head_sha, base_sha=s.base_sha,
    )
    metrics_in = payload.get("metrics")
    if not isinstance(metrics_in, dict):
        metrics_in = {}
    # `s.changed_files` is `list[dict]` when the pipeline seeds it (it
    # converts `RunWorkspace.changed_files` from `list[str]`), but the Copilot
    # pipeline's `_seed_downstream_prepared` seeds the raw `list[str]` unconverted —
    # tolerate both shapes so that upstream gap alone can never crash this tool
    # regardless of the review payload (this was the actual live failure: an
    # AttributeError thrown here, before the artifact was even built).
    def _file_metric(entry: object, key: str) -> float:
        if not isinstance(entry, dict):
            return 0
        val = entry.get(key, 0)
        return val if isinstance(val, (int, float)) else 0

    metrics = ReviewMetrics(
        files_changed=len(s.changed_files),
        added=sum(_file_metric(f, "added") for f in s.changed_files),
        removed=sum(_file_metric(f, "removed") for f in s.changed_files),
        complexity_delta=metrics_in.get("complexity_delta"),
        dupe_delta=metrics_in.get("dupe_delta"),
        debt_delta=metrics_in.get("debt_delta"),
    )

    # Severity/category/status enums self-normalize inside the models (see
    # `code_review.py::_normalize_enum`); what a single model still can't recover
    # from is a missing genuinely-required field (e.g. `description`). Build each
    # entry individually so one malformed item is skipped, not fatal to the batch.
    def _build_all(model, items) -> list:
        built = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                built.append(model(**item))
            except Exception:
                continue
        return built

    findings = _build_all(ReviewFinding, payload.get("findings"))
    requirements_coverage = _build_all(CoverageEntry, payload.get("requirements_coverage"))
    design_conformance = _build_all(ConformanceEntry, payload.get("design_conformance"))

    try:
        artifact = CodeReviewArtifact(
            context=ctx,
            summary=payload.get("summary", ""),
            merge_recommendation=payload.get("merge_recommendation", "needs_discussion"),
            findings=findings,
            requirements_coverage=requirements_coverage,
            design_conformance=design_conformance,
            metrics=metrics,
            diff=s.diff_text,
            status="reviewed",
        )
    except Exception as exc:
        return f"ERROR: review did not match the required shape: {exc}"

    s.last_artifact = artifact.model_dump()
    n = len(artifact.findings)
    crit = sum(1 for f in artifact.findings if f.severity in ("critical", "high"))
    broadcast_log(manager, f"Review complete: {n} findings ({crit} critical/high) → {artifact.merge_recommendation}", level="INFO")
    return f"Review submitted: {n} findings, recommendation={artifact.merge_recommendation}. It will be saved and shown in the Findings/Summary tabs."
