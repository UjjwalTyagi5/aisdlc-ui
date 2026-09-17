"""Code-Review workspace endpoints — open-PR listing, review-target preparation
(clone read-only + compute the diff), and persisted-review listing.

Routes (mounted under '/code-review'):
  GET  /code-review/{project_id}/ado/repos/{ado_project}/{repo}/prs
  POST /code-review/{project_id}/review/prepare
  GET  /code-review/{project_id}/reviews
  GET  /code-review/{project_id}/reviews/{run_id}

The ADO project/repo/branch cascade is reused from the dev-workspace endpoints
(`/dev/{project_id}/ado/...`) — same generic ADO picker. Credentials resolve from
the Azure DevOps connector (Integrations) per tenant, falling back to env.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.agent_access import require_agent_access
from shared.authz.project_scope import require_project_access
from agents_orchestrator.code_review_agent.config.session_state import set_prepared
from shared.db import get_db_session
from shared.models.orm import Run
from shared.services import ado_repos
from shared.services import prepared_targets

# EVERY ROUTE HERE IS SCOPED TO ITS {project_id}. Until 2026-08-17 these handlers
# took the project id from the path and filtered on tenant_id alone, so the only gate
# was the artifact:view floor applied at include time — a permission `contributor`
# holds. The router-level dependency covers all of them at once, and covers whatever
# route is added next. See docs/rbac-audit-2026-08-17.md finding 3.
#
# require_project_access() alone only proves project membership -- it does not
# consult AGENT_DEFAULT_REACH["code_review"], where QA and Data Engineer are "none"
# (PRD §14.7). Without require_agent_access("code_review") too, any project member
# could hit review/prepare and reviews regardless of role -- mirrors
# security_workspace_router's identical two-dependency stack.
code_review_workspace_router = APIRouter(
    dependencies=[
        Depends(require_project_access()),
        Depends(require_agent_access("code_review")),
    ]
)


@code_review_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(
    project_id: str, ado_project: str, repo: str, request: Request,
    provider: str | None = None,
) -> list[dict]:
    """Open pull requests, from whichever host this project's source lives on.

    THE PATH STILL SAYS `ado` and that is now only a name — this serves GitHub too.
    Before it, a GitHub project's PR picker returned an Azure DevOps error.
    """
    from shared.services import repo_source  # noqa: PLC0415

    # project_id + owner_id, like every dev-workspace picker route: the credential may
    # be a project-scoped personal one, which resolves to nothing on tenant_id alone.
    owner_id = str(uid) if (uid := getattr(request.state, "user_id", None)) else ""
    try:
        _chosen, prs = await repo_source.list_pull_requests(
            request.state.tenant_id, ado_project, repo, status="active",
            project_id=project_id, owner_id=owner_id, provider=provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return prs


class PrepareRequest(BaseModel):
    # Where the code lives. Absent means "the project's single configured
    # source", so a one-source project behaves exactly as it always did.
    provider: str | None = None
    mode: str                       # "branch" | "pr" | "repo" (whole branch)
    ado_project: str
    repo_name: str
    source_branch: str | None = None
    base_branch: str | None = None
    pr_id: str | None = None


async def _find_unchanged_review(
    db: AsyncSession, *, tenant_id: str, project_id: str, repo_name: str,
    head_sha: str, base_sha: str, mode: str | None = None,
) -> Run | None:
    """The most recent prior review of this exact diff (same repo, head, base), if any.

    PRD §21.4 (help/Prd (1).md line 307): "Skips redundant re-review when nothing
    changed since the last pass." A diff is unchanged, not merely similar, only when
    both shas match the same repo — comparing on shas rather than source/base branch
    names means a force-push that lands the identical tree still counts as unchanged,
    while a same-named branch that moved does not.

    Filters in Python rather than a JSON-path WHERE clause: `code_review_artifacts` is
    a plain JSON column, `context` is nested inside it, and this project already reads
    it the same way in `_review_summary_row` just above. The last 20 reviews for this
    project is enough headroom for a real target to have moved since a stale match —
    a project running this many reviews without the diff changing is not the case this
    guards for.
    """
    stmt = (
        select(Run)
        .where(
            Run.project_id == uuid.UUID(project_id),
            Run.tenant_id == uuid.UUID(tenant_id),
            Run.code_review_artifacts.isnot(None),
        )
        .order_by(Run.created_at.desc())
        .limit(20)
    )
    rows = (await db.execute(stmt)).scalars().all()
    for run in rows:
        ctx = (run.code_review_artifacts or {}).get("context") or {}
        # A whole-branch review and a diff review of the same commit are different reviews.
        if mode is not None and (ctx.get("mode") == "repo") != (mode == "repo"):
            continue
        if (
            ctx.get("repo_name") == repo_name
            and ctx.get("head_sha") == head_sha
            and ctx.get("base_sha") == base_sha
        ):
            return run
    return None


@code_review_workspace_router.post("/{project_id}/review/prepare")
async def prepare_review(
    project_id: str, body: PrepareRequest, request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Clone the repo read-only and prepare the selected target for review, binding it
    to the chat session. Three targets:

        branch   the changes on source_branch since it left base_branch (a diff)
        pr       an open pull request's changes (a diff)
        repo     a WHOLE BRANCH — every file on source_branch, no diff. For new code,
                 or a branch whose changes are too small to judge it by.

    A diff target with NOTHING in it answers `status: "no_changes"` with the reason and
    binds nothing: an empty diff is not something to review, and a reviewer shown "0
    files changed" cannot tell "no changes" from "something went wrong" — PR #35 in
    QuickLink was two branch names on one commit.
    """
    tenant_id: str = request.state.tenant_id
    # project_id + owner_id, matching dev_workspace.pull_workspace. Passing only the
    # tenant looks at tenant-wide connectors alone, so a project-scoped PERSONAL
    # Azure DevOps credential -- the kind the Integrations page lets a Project Admin
    # save for just their own project -- was never found here. The pickers in the very
    # same dialog (repos, branches) pass both and resolved it fine, so the target
    # selected fine and only "Prepare diff" failed, with a bare 500.
    owner_id = str(uid) if (uid := getattr(request.state, "user_id", None)) else ""
    from shared.services import repo_source  # noqa: PLC0415

    # WHICH HOST, decided once and carried through every call below. Naming a provider
    # you hold no credential for is an error rather than a quiet fall-through to the
    # other one — reviewing the wrong repository is worse than reviewing none.
    #
    # "Not configured" unhandled reached the browser as a bare 500, which reads as a
    # broken agent rather than as a connector nobody has set up. 424 with the resolver's
    # own sentence names the fix and the page that performs it.
    try:
        chosen, _base_url, pat = await repo_source.resolve(
            tenant_id, project_id=project_id, owner_id=owner_id, provider=body.provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc

    if body.mode not in ("branch", "pr", "repo"):
        raise HTTPException(status_code=400, detail="mode must be branch, pr or repo")
    source = (body.source_branch or "").strip()
    base = (body.base_branch or "").strip()
    pr_title = ""
    if body.mode == "repo":
        return await _prepare_whole_branch(
            db, body=body, tenant_id=tenant_id, project_id=project_id, owner_id=owner_id,
            chosen=chosen, pat=pat, branch=source,
        )
    if body.mode == "pr":
        if not body.pr_id:
            raise HTTPException(status_code=400, detail="pr_id required for mode=pr")
        try:
            _p, pr = await repo_source.get_pull_request(
                tenant_id, body.ado_project, body.repo_name, body.pr_id,
                project_id=project_id, owner_id=owner_id, provider=chosen,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=424, detail=str(exc)) from exc
        if not pr:
            raise HTTPException(status_code=404, detail="PR not found")
        source, base, pr_title = pr["source_branch"], pr["target_branch"], pr["title"]
    if not source or not base:
        raise HTTPException(status_code=400, detail="source_branch and base_branch are required")
    if source == base:
        raise HTTPException(status_code=400, detail="source and base branch must differ")

    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "review")
    try:
        result = await repo_source.clone_and_diff(
            tenant_id, body.ado_project, body.repo_name, source, base, work_dir,
            project_id=project_id, owner_id=owner_id, provider=chosen,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not prepare diff: {exc}")
    remote_url = result.get("remote_url", "")

    if not result["files"] and not result.get("commits_ahead"):
        # Nothing to review, said in words. Not bound: "Run review" on an empty diff is
        # a review of nothing.
        where = f"PR #{body.pr_id} ({source} → {base})" if body.mode == "pr" else f"{source} compared with {base}"
        same_commit = result["head_sha"] == result.get("base_tip_sha")
        reason = (
            f"{where} contains no changes: both branches point at the same commit "
            f"({result['head_sha'][:7]})."
            if same_commit else
            f"{where} contains no changes: every commit on {source} is already on {base}."
        )
        return {
            "status": "no_changes",
            "no_changes_reason": reason,
            "mode": body.mode,
            "repo_name": body.repo_name,
            "ado_project": body.ado_project,
            "source_branch": source,
            "base_branch": base,
            "pr_id": body.pr_id,
            "pr_title": pr_title,
            "head_sha": result["head_sha"],
            "base_sha": result["base_sha"],
            "commits_ahead": 0,
            "files": [],
            "diff": "",
            "truncated": False,
            "unchanged_since_last_review": False,
            "existing_review_id": None,
            "existing_review_branch": None,
        }

    _prepared_record = {
        "prepared_at": _now_token(),
        "work_dir": work_dir,
        "repo_url": remote_url,
        "provider": chosen,
        "pat": pat,
        "mode": body.mode,
        "ado_project": body.ado_project,
        "repo_name": body.repo_name,
        "source_branch": source,
        "base_branch": base,
        "pr_id": body.pr_id or "",
        "pr_title": pr_title,
        "head_sha": result["head_sha"],
        "base_sha": result["base_sha"],
        "diff_text": result["diff"],
        "changed_files": result["files"],
        "inventory": {},
        "owner_id": owner_id,
    }
    set_prepared(tenant_id, project_id, _prepared_record)
    # AND ON DISK, so the record outlives this process — see prepared_targets
    # for what a restart used to do to a perfectly good checkout. Never the
    # credential: that is re-resolved as this person when the agent binds.
    prepared_targets.save(
        "code_review", tenant_id, project_id, {**_prepared_record}, owner_id=owner_id,
    )

    unchanged = await _find_unchanged_review(
        db, tenant_id=tenant_id, project_id=project_id, repo_name=body.repo_name,
        head_sha=result["head_sha"], base_sha=result["base_sha"],
    )
    # The branch the matching review was run against. Usually the same branch, but
    # two branch names can point at one commit -- and then "nothing changed" reads as
    # "it ignored the branch I picked" unless the UI can name the branch it matched.
    unchanged_branch = (
        ((unchanged.code_review_artifacts or {}).get("context") or {}).get("source_branch")
        if unchanged is not None else None
    )

    return {
        "status": "ready",
        "no_changes_reason": None,
        "mode": body.mode,
        "repo_name": body.repo_name,
        "ado_project": body.ado_project,
        "source_branch": source,
        "base_branch": base,
        "pr_id": body.pr_id,
        "pr_title": pr_title,
        "head_sha": result["head_sha"],
        "base_sha": result["base_sha"],
        "commits_ahead": result.get("commits_ahead", 0),
        "files": result["files"],
        "diff": result["diff"],
        "truncated": result["truncated"],
        "unchanged_since_last_review": unchanged is not None,
        "existing_review_id": str(unchanged.id) if unchanged is not None else None,
        "existing_review_branch": unchanged_branch,
    }


def _now_token() -> str:
    """When a target was prepared — how the chat notices a NEW target in the same session."""
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).isoformat()


async def _prepare_whole_branch(
    db: AsyncSession, *, body: "PrepareRequest", tenant_id: str, project_id: str,
    owner_id: str, chosen: str, pat: str, branch: str,
) -> dict:
    """Clone one branch and list its files — the whole-branch review target."""
    from shared.services import repo_source  # noqa: PLC0415
    from shared.services.branch_inventory import branch_inventory  # noqa: PLC0415

    if not branch:
        raise HTTPException(status_code=400, detail="source_branch is required for a whole-branch review")
    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "review")
    try:
        result = await repo_source.clone(
            tenant_id, body.ado_project, body.repo_name, branch, work_dir,
            project_id=project_id, owner_id=owner_id, provider=chosen,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}") from exc
    head_sha = result.get("commit_sha") or ""
    if not head_sha:
        raise HTTPException(status_code=502, detail=f"The branch {branch} was cloned but its commit could not be read.")
    try:
        inventory = await asyncio.to_thread(branch_inventory, work_dir)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    record = {
        "prepared_at": _now_token(),
        "work_dir": work_dir,
        "repo_url": result.get("remote_url", ""),
        "provider": chosen,
        "pat": pat,
        "mode": "repo",
        "ado_project": body.ado_project,
        "repo_name": body.repo_name,
        "source_branch": branch,
        "base_branch": "",
        "pr_id": "",
        "pr_title": "",
        "head_sha": head_sha,
        "base_sha": "",
        "diff_text": "",
        "changed_files": [],
        "inventory": inventory,
        "owner_id": owner_id,
    }
    set_prepared(tenant_id, project_id, record)
    prepared_targets.save("code_review", tenant_id, project_id, {**record}, owner_id=owner_id)

    unchanged = await _find_unchanged_review(
        db, tenant_id=tenant_id, project_id=project_id, repo_name=body.repo_name,
        head_sha=head_sha, base_sha="", mode="repo",
    )
    files = [
        {"path": f["path"], "status": "T", "added": f["lines"], "removed": 0,
         "language": f["language"], "reviewable": f["reviewable"]}
        for f in inventory["files"]
    ]
    return {
        "status": "ready",
        "no_changes_reason": None,
        "mode": "repo",
        "repo_name": body.repo_name,
        "ado_project": body.ado_project,
        "source_branch": branch,
        "base_branch": "",
        "pr_id": None,
        "pr_title": "",
        "head_sha": head_sha,
        "base_sha": "",
        "commits_ahead": 0,
        "files": files,
        "diff": "",
        "truncated": False,
        "inventory_totals": inventory["totals"],
        "languages": inventory["languages"],
        "unchanged_since_last_review": unchanged is not None,
        "existing_review_id": str(unchanged.id) if unchanged is not None else None,
        "existing_review_branch": (
            ((unchanged.code_review_artifacts or {}).get("context") or {}).get("source_branch")
            if unchanged is not None else None
        ),
    }


def _review_summary_row(run: Run) -> dict:
    art = run.code_review_artifacts or {}
    ctx = art.get("context") or {}
    findings = art.get("findings") or []
    crit = sum(1 for f in findings if f.get("severity") in ("critical", "high"))
    if ctx.get("mode") == "pr" and ctx.get("pr_id"):
        label = f"PR #{ctx.get('pr_id')}"
    elif ctx.get("mode") == "repo":
        label = f"{ctx.get('source_branch', '')} (whole branch)"
    else:
        label = f"{ctx.get('source_branch', '')} → {ctx.get('base_branch', '')}"
    return {
        "id": str(run.id),
        "label": label,
        "repo_name": ctx.get("repo_name", ""),
        "merge_recommendation": art.get("merge_recommendation", "needs_discussion"),
        "findings_count": len(findings),
        "critical_high": crit,
        "created_at": run.created_at.isoformat(),
    }


@code_review_workspace_router.get("/{project_id}/reviews")
async def list_reviews(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    tenant_id: str = request.state.tenant_id
    stmt = (
        select(Run)
        .where(
            Run.project_id == uuid.UUID(project_id),
            Run.tenant_id == uuid.UUID(tenant_id),
            Run.code_review_artifacts.isnot(None),
        )
        .order_by(Run.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_review_summary_row(r) for r in rows]


@code_review_workspace_router.get("/{project_id}/reviews/{run_id}")
async def get_review(
    project_id: str, run_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict:
    tenant_id: str = request.state.tenant_id
    run = (
        await db.execute(
            select(Run).where(
                Run.id == uuid.UUID(run_id),
                Run.project_id == uuid.UUID(project_id),
                Run.tenant_id == uuid.UUID(tenant_id),
            )
        )
    ).scalar_one_or_none()
    if run is None or not run.code_review_artifacts:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"id": str(run.id), "created_at": run.created_at.isoformat(), **run.code_review_artifacts}
