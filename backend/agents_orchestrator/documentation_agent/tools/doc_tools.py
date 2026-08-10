"""Native tools for the standalone Documentation agent.

The branch/PR under documentation is cloned by the API and bound to the session.
These tools let the agent inspect the repo, read git history, read this project's
upstream platform artifacts, SAVE generated documents to the local docs folder
(surfaced in the page's left-side list), and optionally open a gated docs PR.
Read-only on the repo except the explicit open_docs_pr.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import shutil
import subprocess
import uuid

from langchain_core.tools import tool

from agents_orchestrator.documentation_agent.config.session_state import get_session
from config.connection_manager import manager
from config.env import DOCS_OUTPUT_ROOT
from config.ws_helper import broadcast_log, get_session_id

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", "dist", "build", ".next"}
_MAX_FILE_BYTES = 200_000
_GIT_BIN = shutil.which("git")

_TYPE_BUCKETS = {
    "feat": "Added", "add": "Added", "feature": "Added",
    "change": "Changed", "refactor": "Changed", "perf": "Changed", "chore": "Changed",
    "fix": "Fixed", "bug": "Fixed", "bugfix": "Fixed",
    "security": "Security", "sec": "Security",
}


def _work_dir() -> pathlib.Path | None:
    s = get_session(get_session_id())
    return pathlib.Path(s.work_dir) if s.work_dir else None


@tool
async def read_repo_file(path: str) -> str:
    """Read one repo-relative file from the cloned repo (source file, config, README,
    OpenAPI spec, etc.) to ground the documentation."""
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared. Ask the user to select a branch/PR first."
    try:
        target = (root / path).resolve()
        target.relative_to(root.resolve())
    except ValueError:
        return "ERROR: path traversal denied."
    if not target.exists() or not target.is_file():
        return f"ERROR: file not found: {path}"
    return target.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")


@tool
async def search_repo(query: str, max_results: int = 50) -> str:
    """Grep the cloned repo for a literal string (find endpoints, routes, classes,
    config keys, version strings)."""
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared."
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


@tool
async def inspect_repo() -> str:
    """Summarize the checked-out repo for documentation: detected languages, top-level
    structure, and notable files (READMEs, API specs, manifests, project files).
    Call this first."""
    s = get_session(get_session_id())
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared."

    ext_counts: dict[str, int] = {}
    notable: dict[str, list[str]] = {
        "readme": [], "openapi": [], "project_files": [], "entrypoints": [], "docs": [],
    }
    top_level: list[str] = []
    root_resolved = root.resolve()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        rel_dir = str(pathlib.Path(dirpath).relative_to(root)).replace("\\", "/")
        if rel_dir == ".":
            top_level = sorted(dirs)[:40]
        low_dir = rel_dir.lower()
        for fn in files:
            rel = (f"{rel_dir}/{fn}" if rel_dir != "." else fn)
            l = fn.lower()
            ext = pathlib.Path(fn).suffix.lower()
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
            if l.startswith("readme"):
                notable["readme"].append(rel)
            elif l in ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml"):
                notable["openapi"].append(rel)
            elif l in ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "pom.xml", "cargo.toml") or l.endswith(".csproj"):
                notable["project_files"].append(rel)
            elif l in ("main.py", "app.py", "index.ts", "index.js", "program.cs", "main.go"):
                notable["entrypoints"].append(rel)
            elif "docs" in low_dir and ext in (".md", ".mdx", ".rst"):
                notable["docs"].append(rel)

    _LANG = {
        ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
        ".jsx": "JavaScript", ".cs": "C#", ".go": "Go", ".java": "Java", ".rb": "Ruby",
        ".rs": "Rust", ".php": "PHP", ".kt": "Kotlin", ".cpp": "C++", ".c": "C",
    }
    langs = sorted(
        {_LANG[e] for e in ext_counts if e in _LANG},
        key=lambda name: -sum(ext_counts[e] for e in ext_counts if _LANG.get(e) == name),
    )
    s.languages = langs
    notable = {k: v[:15] for k, v in notable.items()}
    return json.dumps({
        "repo_name": s.repo_name,
        "branch": s.source_branch,
        "languages": langs,
        "top_level": top_level,
        "notable_files": notable,
        "upstream_summary": s.upstream_summary or "(no upstream platform artifacts for this project)",
    })


@tool
async def generate_changelog(since_ref: str = "") -> str:
    """Generate a grouped changelog from the cloned repo's git history.

    Args:
        since_ref: optional git ref (tag/commit) to start from; empty = last 80 commits.
    """
    root = _work_dir()
    if root is None or not root.exists():
        return json.dumps({"status": "error", "message": "no workspace prepared", "changelog": ""})
    if not _GIT_BIN:
        return json.dumps({"status": "unavailable", "message": "git CLI not installed", "changelog": ""})

    cmd = [_GIT_BIN, "-C", str(root), "log", "--no-merges", "--pretty=format:%s"]
    if since_ref:
        cmd.append(f"{since_ref}..HEAD")
    else:
        cmd.extend(["-n", "80"])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return json.dumps({"status": "error", "message": f"git log failed: {(result.stderr or '')[:300]}", "changelog": ""})
        subjects = [s for s in result.stdout.splitlines() if s.strip()]
        buckets: dict[str, list[str]] = {"Added": [], "Changed": [], "Fixed": [], "Security": []}
        for subj in subjects:
            head = subj.split(":", 1)[0].strip().lower().split("(", 1)[0].strip()
            buckets[_TYPE_BUCKETS.get(head, "Changed")].append(subj)
        lines: list[str] = []
        for label in ("Added", "Changed", "Fixed", "Security"):
            if buckets[label]:
                lines.append(f"### {label}")
                lines.extend(f"- {s}" for s in buckets[label])
                lines.append("")
        return json.dumps({"status": "ok", "commit_count": len(subjects), "changelog": "\n".join(lines).strip()})
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "message": "git log timed out", "changelog": ""})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"changelog failed: {str(e)[:300]}", "changelog": ""})


@tool
async def read_upstream_artifacts() -> str:
    """Read this project's latest platform artifacts (requirements, design, development,
    testing, code review, security), if any pipeline has produced them. Returns a JSON
    object keyed by artifact; values are null when absent."""
    s = get_session(get_session_id())
    cols = {
        "requirements": "requirements_payload",
        "design": "design_artifacts",
        "development": "development_artifacts",
        "testing": "testing_artifacts",
        "code_review": "code_review_artifacts",
        "security": "security_artifacts",
    }
    out: dict = {k: None for k in cols}
    if not s.tenant_id or not s.project_id:
        return json.dumps(out)
    from sqlalchemy import select
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    try:
        async with get_db_session_for_tenant(s.tenant_id) as db:
            for key, col in cols.items():
                row = (
                    await db.execute(
                        select(getattr(Run, col))
                        .where(Run.project_id == uuid.UUID(s.project_id), getattr(Run, col).isnot(None))
                        .order_by(Run.created_at.desc()).limit(1)
                    )
                ).scalars().first()
                if row:
                    out[key] = row
    except Exception:
        pass
    return json.dumps(out, default=str)[:20000]


def _output_dir(s) -> pathlib.Path:
    d = pathlib.Path(DOCS_OUTPUT_ROOT) / (s.project_id or "no-project") / (get_session_id() or "no-session")
    d.mkdir(parents=True, exist_ok=True)
    return d


@tool
async def save_document(doc_type: str, title: str, filename: str, markdown_contents: str) -> str:
    """Save ONE finished document. Writes it to the docs folder and surfaces it in the
    user's left-side document list. Call once per deliverable.

    Args:
        doc_type: one of overview|sdd|api_reference|code_summary|changelog|release_notes|rtm|run_summary|compliance|doc_set|custom
        title: human-readable title (shown in the list)
        filename: kebab-case filename ending in .md (e.g. "api-reference.md")
        markdown_contents: the full GitHub-flavored Markdown body
    """
    s = get_session(get_session_id())
    if not markdown_contents or not markdown_contents.strip():
        return "ERROR: refusing to save an empty document."
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", (filename or title or "document")).strip("-") or "document"
    if not safe.lower().endswith((".md", ".markdown")):
        safe += ".md"
    try:
        out_dir = _output_dir(s)
        path = out_dir / safe
        path.write_text(markdown_contents, encoding="utf-8")
    except Exception as exc:
        return f"ERROR saving document: {str(exc)[:300]}"

    doc_id = uuid.uuid4().hex[:10]
    s.generated_docs = [d for d in s.generated_docs if d.get("filename") != safe]
    s.generated_docs.append({
        "id": doc_id,
        "type": doc_type if doc_type in {
            "overview", "sdd", "api_reference", "code_summary", "changelog",
            "release_notes", "rtm", "run_summary", "compliance", "doc_set", "custom",
        } else "custom",
        "title": title or safe,
        "filename": safe,
        "format": "md",
        "path": str(path),
        "contents": markdown_contents,
        "bytes": len(markdown_contents.encode("utf-8")),
    })
    broadcast_log(manager, f"Generated document: {title or safe} ({safe})", level="SUCCESS")
    return f"Saved '{title or safe}' as {safe} ({len(markdown_contents)} chars). It now appears in the user's document list. {len(s.generated_docs)} document(s) generated this session."


@tool
async def open_docs_pr(title: str = "", description: str = "") -> str:
    """Open a documentation PR with all saved documents committed under docs/ (GATED —
    only call when the user explicitly asks to open/create a docs PR). Pushes a new
    branch and opens a PR against the source branch."""
    from shared.services import ado_repos

    s = get_session(get_session_id())
    if not s.generated_docs:
        return "ERROR: no documents generated yet. Generate at least one document first."
    if not (s.work_dir and s.repo_name and s.source_branch):
        return "ERROR: no prepared target. Select a branch/PR first."
    new_branch = f"docs/auto-{uuid.uuid4().hex[:8]}"
    files = [(f"docs/{d['filename']}", d["contents"]) for d in s.generated_docs]
    pr_title = title or f"Documentation for {s.repo_name} ({s.source_branch})"
    try:
        await asyncio.to_thread(
            ado_repos.commit_and_push_files, s.work_dir, s.source_branch, new_branch,
            files, s.pat, pr_title,
        )
        pr_url = await ado_repos.create_pull_request(
            s.ado_project or s.repo_name, s.repo_name, new_branch, s.source_branch,
            pr_title, description or "Generated documentation set.", pat=s.pat,
            tenant_id=s.tenant_id,
        )
    except Exception as exc:
        return f"ERROR opening docs PR: {str(exc)[:400]}"
    if not pr_url:
        return "ERROR: pushed the branch but could not open the PR (repo not found?)."
    s.pr_url = pr_url
    broadcast_log(manager, f"Opened documentation PR: {pr_url}", level="INFO")
    return f"Documentation PR opened: {pr_url} (branch {new_branch} → {s.source_branch}, {len(files)} files)."
