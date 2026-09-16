"""Code Review: whole-branch reviews, an honest empty diff, and the security review.

THE PR THAT PROMPTED THIS. QuickLink's PR #35 showed zero changes: its source branch and
target branch were two names on one commit. That was true — but the page showed the same
empty diff a failed git command would have produced, and there was nothing the reviewer
could do next. A branch of new code, or one whose change is too small to judge the code by,
had no way to be reviewed at all.

Pinned here:
- git failures raise instead of producing an empty diff; the commits ahead are counted;
- a diff target with nothing in it is answered "no changes" with the reason, and not bound;
- a WHOLE BRANCH can be prepared: cloned, its files listed, bound for review;
- the security review is produced by the scanners over the whole checkout — each scanner's
  status recorded, dependency versions resolved, transitive packages traced to the direct
  dependency that brings them in;
- the review cannot be submitted without it, and records what was actually read.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A local repo with main, a branch at the same commit, and a branch one commit ahead."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "src").mkdir()
    (root / "src" / "app.js").write_text("const x = 1;\nmodule.exports = x;\n", encoding="utf-8")
    (root / "src" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"dependencies": {"express": "^4.19.2"}}), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    _git(root, "branch", "same-as-main")
    _git(root, "checkout", "-b", "feature")
    (root / "src" / "app.js").write_text("const x = 2;\nmodule.exports = x;\n", encoding="utf-8")
    _git(root, "commit", "-am", "change")
    _git(root, "checkout", "main")
    return root


# ── the diff: checked, counted ───────────────────────────────────────────────


def test_a_branch_at_the_same_commit_has_no_changes_and_says_so(repo):
    from shared.services.ado_repos import diff_refs

    result = diff_refs(str(repo), "same-as-main", "main")
    assert result["files"] == [] and result["diff"] == ""
    assert result["commits_ahead"] == 0
    assert result["head_sha"] == result["base_tip_sha"]


def test_a_branch_with_a_commit_lists_its_change(repo):
    from shared.services.ado_repos import diff_refs

    result = diff_refs(str(repo), "feature", "main")
    assert result["commits_ahead"] == 1
    assert [f["path"] for f in result["files"]] == ["src/app.js"]
    assert result["files"][0]["added"] == 1 and result["files"][0]["removed"] == 1


def test_a_git_failure_raises_instead_of_looking_like_no_changes(repo):
    from shared.services.ado_repos import diff_refs

    with pytest.raises(RuntimeError, match="Resolving"):
        diff_refs(str(repo), "no-such-branch", "main")


# ── the whole branch ─────────────────────────────────────────────────────────


def test_the_branch_inventory_lists_tracked_files_and_what_is_reviewable(repo):
    from shared.services.branch_inventory import branch_inventory

    (repo / "untracked.js").write_text("x", encoding="utf-8")
    inv = branch_inventory(str(repo))
    paths = {f["path"]: f for f in inv["files"]}
    assert set(paths) == {"src/app.js", "src/.gitkeep", "package.json"}, "untracked files are not the branch"
    assert paths["src/app.js"]["language"] == "JavaScript" and paths["src/app.js"]["lines"] == 2
    assert paths["src/.gitkeep"]["reviewable"] is False
    assert inv["totals"]["reviewable_files"] == 2


def test_a_non_repo_is_an_error_not_an_empty_branch(tmp_path):
    from shared.services.branch_inventory import branch_inventory

    with pytest.raises(RuntimeError):
        branch_inventory(str(tmp_path))


def _request(tenant="t1", user="u1"):
    return SimpleNamespace(state=SimpleNamespace(tenant_id=tenant, user_id=user))


@pytest.mark.asyncio
async def test_a_whole_branch_is_prepared_bound_and_listed(repo, tmp_path, monkeypatch):
    from shared.routers import code_review_workspace as cr
    from shared.services import ado_repos

    monkeypatch.setattr(ado_repos, "WORKSPACE_ROOT", tmp_path / "ws")
    saved = {}

    async def _clone(tenant, ns, repo_name, branch, work_dir, **kw):
        import shutil

        shutil.copytree(repo, work_dir, dirs_exist_ok=True)
        _git(work_dir, "checkout", branch)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True).stdout.strip()
        return {"commit_sha": sha, "remote_url": "https://example/repo"}

    with patch("shared.services.repo_source.resolve", AsyncMock(return_value=("ado", "", "pat"))), \
         patch("shared.services.repo_source.clone", _clone), \
         patch.object(cr, "set_prepared", lambda t, p, rec: saved.update(rec)), \
         patch.object(cr.prepared_targets, "save", lambda *a, **k: None), \
         patch.object(cr, "_find_unchanged_review", AsyncMock(return_value=None)):
        out = await cr.prepare_review(
            "p1", cr.PrepareRequest(mode="repo", ado_project="QuickLink", repo_name="QuickLink", source_branch="feature"),
            _request(), db=None,
        )

    assert out["status"] == "ready" and out["mode"] == "repo" and out["diff"] == ""
    assert {f["path"] for f in out["files"]} == {"src/app.js", "src/.gitkeep", "package.json"}
    assert out["inventory_totals"]["reviewable_files"] == 2
    assert saved["mode"] == "repo" and saved["inventory"]["totals"]["files"] == 3 and saved["prepared_at"]


@pytest.mark.asyncio
async def test_an_empty_pull_request_is_answered_no_changes_and_not_bound(tmp_path, monkeypatch):
    from shared.routers import code_review_workspace as cr
    from shared.services import ado_repos

    monkeypatch.setattr(ado_repos, "WORKSPACE_ROOT", tmp_path / "ws")
    bound = []
    same = "082f91e49bf0ffb68b063d28db7c89096b5eb247"
    empty = {"diff": "", "files": [], "head_sha": same, "base_sha": same, "base_tip_sha": same,
             "commits_ahead": 0, "truncated": False, "remote_url": "https://example/repo"}
    pr = {"source_branch": "feature/116-117-link-management", "target_branch": "feature/quicklink-initial", "title": "feat"}

    with patch("shared.services.repo_source.resolve", AsyncMock(return_value=("ado", "", "pat"))), \
         patch("shared.services.repo_source.get_pull_request", AsyncMock(return_value=("ado", pr))), \
         patch("shared.services.repo_source.clone_and_diff", AsyncMock(return_value=empty)), \
         patch.object(cr, "set_prepared", lambda *a: bound.append(a)), \
         patch.object(cr.prepared_targets, "save", lambda *a, **k: bound.append(a)):
        out = await cr.prepare_review(
            "p1", cr.PrepareRequest(mode="pr", ado_project="QuickLink", repo_name="QuickLink", pr_id="35"),
            _request(), db=None,
        )

    assert out["status"] == "no_changes"
    assert "PR #35" in out["no_changes_reason"] and "same commit (082f91e)" in out["no_changes_reason"]
    assert bound == [], "an empty diff is not bound for review"


def test_a_whole_branch_review_is_labelled_as_one():
    from shared.routers.code_review_workspace import _review_summary_row

    run = SimpleNamespace(id="r1", created_at=SimpleNamespace(isoformat=lambda: "2026-09-16T00:00:00"), code_review_artifacts={
        "context": {"mode": "repo", "source_branch": "feature/x", "repo_name": "QuickLink"},
        "merge_recommendation": "approve", "findings": [],
    })
    assert _review_summary_row(run)["label"] == "feature/x (whole branch)"


# ── the security review ──────────────────────────────────────────────────────


LOCK = {
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "app", "dependencies": {"sqlite3": "^5.1.7", "express": "^4.19.2"}, "devDependencies": {"jest": "^29"}},
        "node_modules/sqlite3": {"version": "5.1.7", "license": "BSD-3-Clause", "dependencies": {"tar": "^6.1.11"}},
        "node_modules/tar": {"version": "6.2.1", "license": "ISC", "dependencies": {"minipass": "^5"}},
        "node_modules/tar/node_modules/minipass": {"version": "5.0.0", "license": "ISC"},
        "node_modules/minipass": {"version": "7.0.0", "license": "ISC"},
        "node_modules/express": {"version": "4.22.3", "license": "MIT"},
        "node_modules/jest": {"version": "29.7.0", "license": "MIT", "dev": True},
    },
}


def test_the_lockfile_traces_every_package_to_the_direct_dependency_that_brings_it(tmp_path):
    from shared.services.code_security_scan import _lock_packages

    lock = tmp_path / "package-lock.json"
    lock.write_text(json.dumps(LOCK), encoding="utf-8")
    direct, installed = _lock_packages(lock)
    assert direct == {"sqlite3": "5.1.7", "express": "4.22.3", "jest": "29.7.0"}
    by = {(p["name"], p["version"]): p for p in installed}
    assert by[("tar", "6.2.1")]["via"] == "sqlite3" and not by[("tar", "6.2.1")]["direct"]
    assert by[("minipass", "5.0.0")]["via"] == "sqlite3", "the nested copy tar resolves, not the top-level one"
    assert by[("jest", "29.7.0")]["scope"] == "development"


def _tool(result: dict):
    return SimpleNamespace(func=lambda target_path: json.dumps(result))


def test_the_scan_records_each_scanner_resolves_versions_and_traces_vulnerabilities(repo, tmp_path, monkeypatch):
    from shared.services import code_security_scan as scan

    resolved = tmp_path / "resolved"
    resolved.mkdir()
    (resolved / "package-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")
    monkeypatch.setattr(scan, "_resolve_npm", lambda manifest, scratch: (resolved, ""))
    trivy_calls = []

    def _trivy(target_path):
        trivy_calls.append(target_path)
        findings = [{"cve": "CVE-1", "severity": "HIGH", "package": "tar", "installed_version": "6.2.1",
                     "fixed_version": "7.5.3", "title": "tar: bad", "target": "package-lock.json"}] \
            if target_path == str(resolved) else []
        return json.dumps({"status": "ok", "findings": findings})

    with patch("agents_orchestrator.security_agent.tools.gitleaks_tool.run_gitleaks_scan",
               _tool({"status": "ok", "findings": [{"rule_id": "aws", "description": "AWS key", "file": str(repo / "src/app.js"), "line": 1}]})), \
         patch("agents_orchestrator.security_agent.tools.semgrep_sast_tool.run_semgrep_sast",
               _tool({"status": "unavailable", "message": "Semgrep CLI is not installed", "findings": []})), \
         patch("agents_orchestrator.security_agent.tools.trivy_tool.run_trivy_scan", SimpleNamespace(func=_trivy)):
        result = scan.run_code_security_scan(str(repo))

    status = {s["name"]: s for s in result["scanners"]}
    assert status["Semgrep"]["status"] == "unavailable" and status["Semgrep"]["findings"] is None, "not run is not zero"
    assert result["secrets"] == [{"rule": "aws", "description": "AWS key", "file": "src/app.js", "line": 1}]
    assert str(resolved) in trivy_calls, "the resolved lockfile is what Trivy scans"
    comps = {c["name"]: c for c in result["sbom"]["components"]}
    assert comps["express"]["declared"] == "^4.19.2" and comps["express"]["version"] == "4.22.3"
    assert comps["express"]["version_source"] == "resolved at scan time"
    assert comps["tar"]["via"] == "sqlite3" and comps["tar"]["vulnerabilities"] == 1
    assert any("no lockfile is committed" in n for n in result["sbom"]["notes"])
    assert result["totals"]["vulnerabilities_high"] == 1 and result["totals"]["scanners_failed"] == 1
    assert (repo / "package-lock.json").exists() is False, "the checkout is never modified"


def test_a_failed_version_resolution_is_stated_not_hidden(repo, monkeypatch):
    from shared.services import code_security_scan as scan

    monkeypatch.setattr(scan, "_resolve_npm", lambda manifest, scratch: (None, "npm could not resolve: E404"))
    ok = _tool({"status": "ok", "findings": []})
    with patch("agents_orchestrator.security_agent.tools.gitleaks_tool.run_gitleaks_scan", ok), \
         patch("agents_orchestrator.security_agent.tools.semgrep_sast_tool.run_semgrep_sast", ok), \
         patch("agents_orchestrator.security_agent.tools.trivy_tool.run_trivy_scan", ok):
        result = scan.run_code_security_scan(str(repo))
    comps = {c["name"]: c for c in result["sbom"]["components"]}
    assert comps["express"]["version_source"] == "declared range only" and comps["express"]["version"] == ""
    assert any("E404" in n for n in result["sbom"]["notes"])


# ── the agent's tools ────────────────────────────────────────────────────────


@pytest.fixture
def review_session(repo):
    from agents_orchestrator.code_review_agent.config.session_state import get_session
    from config.ws_helper import set_session_id
    from shared.services.branch_inventory import branch_inventory

    sid = f"cr-{repo.name}-{id(repo)}"
    set_session_id(sid)
    s = get_session(sid)
    s.work_dir, s.mode, s.repo_name, s.source_branch, s.head_sha = str(repo), "repo", "QuickLink", "main", "abc1234"
    s.inventory = branch_inventory(str(repo))
    s.files_read, s.security, s.last_artifact = [], None, None
    return s


@pytest.mark.asyncio
async def test_a_review_cannot_be_submitted_without_the_security_review(review_session):
    from agents_orchestrator.code_review_agent.tools.review_tools import submit_code_review

    out = await submit_code_review.ainvoke({"review_json": json.dumps({"summary": "x", "merge_recommendation": "approve"})})
    assert out.startswith("ERROR") and "run_security_review" in out
    assert review_session.last_artifact is None


@pytest.mark.asyncio
async def test_the_submitted_review_carries_the_scan_and_what_was_read(review_session):
    from agents_orchestrator.code_review_agent.tools.review_tools import read_repo_file, submit_code_review

    await read_repo_file.ainvoke({"path": "src/app.js"})
    review_session.security = {"scanners": [], "totals": {"vulnerabilities": 0}, "sbom": {"components": []}}
    out = await submit_code_review.ainvoke({"review_json": json.dumps({
        "summary": "Small app.", "merge_recommendation": "approve",
        "security_summary": "Nothing found.", "findings": [],
    })})

    assert "Review submitted" in out
    art = review_session.last_artifact
    assert art["context"]["mode"] == "repo"
    assert art["security"] == review_session.security and art["security_summary"] == "Nothing found."
    assert art["scope"]["files_read"] == ["src/app.js"]
    assert art["scope"]["reviewable_files_read"] == 1 and art["scope"]["not_read"] == ["package.json"]


@pytest.mark.asyncio
async def test_list_repo_files_serves_the_inventory(review_session):
    from agents_orchestrator.code_review_agent.tools.review_tools import list_repo_files

    data = json.loads(await list_repo_files.ainvoke({}))
    assert data["totals"]["files"] == 3 and {f["path"] for f in data["files"]} >= {"src/app.js"}


def test_the_whole_branch_context_lists_the_files_not_a_diff(review_session):
    from agents_orchestrator.code_review_agent.code_review_agent_api import _review_context_block

    block = _review_context_block(review_session)
    assert "WHOLE BRANCH 'main'" in block and "src/app.js (2 lines, JavaScript)" in block
    assert "```diff" not in block


def test_the_agent_has_the_new_tools_and_is_told_to_use_them():
    from agents_orchestrator.code_review_agent.agents import reviewer
    from agents_orchestrator.code_review_agent.prompts.review_prompt import CODE_REVIEW_SYSTEM_PROMPT

    names = {t.name for t in reviewer._tools}
    assert {"run_security_review", "list_repo_files", "raise_document_for_approval"} <= names
    assert "WHOLE BRANCH" in CODE_REVIEW_SYSTEM_PROMPT and "run_security_review" in CODE_REVIEW_SYSTEM_PROMPT
    assert "security_summary" in CODE_REVIEW_SYSTEM_PROMPT
