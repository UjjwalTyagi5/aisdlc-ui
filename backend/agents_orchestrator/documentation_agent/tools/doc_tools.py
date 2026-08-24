"""Native tools for the standalone Documentation agent.

The branch/PR under documentation is cloned by the API and bound to the session.
These tools let the agent inspect the repo, read git history, read this project's
upstream platform artifacts, SAVE generated documents to the local docs folder
(surfaced in the page's left-side list), and optionally open a gated docs PR.
Read-only on the repo except the explicit open_docs_pr.
"""
from __future__ import annotations

import asyncio
import difflib
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
        doc_type: one of overview|sdd|api_reference|code_summary|changelog|release_notes|rtm|run_summary|compliance|doc_set|runbook_update|knowledge_article|custom
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
            "runbook_update", "knowledge_article",
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


async def _sharepoint_session():
    """Return (connector, target) for this session's tenant, or (None, reason).

    Additive to the existing outputs: save_document still writes to local disk and
    open_docs_pr still files a git PR. SharePoint is a third destination, not a
    replacement for either.
    """
    s = get_session(get_session_id())
    tenant_id = getattr(s, "tenant_id", "") or ""
    if not tenant_id:
        return None, "ERROR: no tenant context in this session."
    try:
        from shared.services.notification_targets import sharepoint_target
        from config.connector_factory import get_connector_for_session

        target = await sharepoint_target(tenant_id)
        if not target:
            return None, (
                "ERROR: SharePoint is not connected for this tenant. An admin can "
                "connect it on the Integrations page (Documents & knowledge)."
            )
        connector = await get_connector_for_session(kind="sharepoint", tenant_id=tenant_id)
        return (connector, target), ""
    except Exception as exc:
        return None, f"ERROR reaching SharePoint: {type(exc).__name__}"


@tool
async def publish_to_sharepoint(filename: str = "", folder: str = "") -> str:
    """Publish saved documents to the project's SharePoint document library (GATED —
    only call when the user explicitly asks to publish/file documents to SharePoint).

    Args:
        filename: publish only this document (as named by save_document). Omit to
                  publish every document generated this session.
        folder:   override the configured library folder.
    """
    s = get_session(get_session_id())
    if not s.generated_docs:
        return "ERROR: no documents generated yet. Generate at least one document first."

    resolved, reason = await _sharepoint_session()
    if not resolved:
        return reason
    connector, target = resolved

    docs = s.generated_docs
    if filename:
        docs = [d for d in docs if d.get("filename") == filename]
        if not docs:
            return f"ERROR: no generated document named {filename!r} in this session."

    drive_id = target.get("drive_id", "")
    base_folder = (folder or target.get("folder") or "").strip("/")

    published, failures = [], []
    for doc in docs:
        name = doc.get("filename") or "document.md"
        path = f"{base_folder}/{name}" if base_folder else name
        try:
            result = await connector.write_adapter(
                "publish_document",
                drive_id=drive_id,
                path=path,
                content=(doc.get("contents") or "").encode("utf-8"),
                content_type="text/markdown",
            )
            published.append((name, result.get("webUrl", "")))
            doc["sharepoint_url"] = result.get("webUrl", "")
        except ValueError as exc:
            # Over the 4 MB single-PUT ceiling, or a bad path — actionable, so say it.
            failures.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}")

    if published:
        broadcast_log(
            manager,
            f"Published {len(published)} document(s) to SharePoint",
            level="SUCCESS",
        )
    if not published:
        return "ERROR publishing to SharePoint: " + "; ".join(failures)

    lines = [f"Published {len(published)} document(s) to SharePoint:"]
    lines += [f"- {n}{f' — {u}' if u else ''}" for n, u in published]
    if failures:
        lines.append(f"{len(failures)} failed: " + "; ".join(failures))
    return "\n".join(lines)


@tool
async def list_sharepoint_documents(folder: str = "") -> str:
    """List the documents already filed in the project's SharePoint library."""
    resolved, reason = await _sharepoint_session()
    if not resolved:
        return reason
    connector, target = resolved
    try:
        items = await connector.read_adapter(
            "list_documents",
            drive_id=target.get("drive_id", ""),
            folder=folder or target.get("folder", ""),
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR listing SharePoint documents: {type(exc).__name__}"
    if not items:
        return "The SharePoint library folder is empty."
    lines = [f"{len(items)} item(s) in the SharePoint library:"]
    for it in items[:50]:
        web_url = it.get("webUrl", "")
        suffix = f" — {web_url}" if web_url else ""
        lines.append(
            f"- {it.get('name', '?')} (id={it.get('id', '')}, {it.get('size', 0)} bytes){suffix}"
        )
    return "\n".join(lines)


@tool
async def ingest_sharepoint_document(item_id: str) -> str:
    """Read one SharePoint document into this session as reference material.

    Use it to pull an existing specification or standard out of the business's
    document library before writing new documentation. Text is extracted with the
    platform's existing extractor, so pdf/docx/txt/md/csv/xlsx all work.
    """
    if not item_id:
        return "ERROR: item_id is required — call list_sharepoint_documents first."
    resolved, reason = await _sharepoint_session()
    if not resolved:
        return reason
    connector, target = resolved

    s = get_session(get_session_id())
    try:
        data = await connector.read_adapter(
            "download_document", drive_id=target.get("drive_id", ""), item_id=item_id
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR downloading SharePoint document: {type(exc).__name__}"

    try:
        from shared.tools.document_tools import extract_file_text

        tmp_dir = _output_dir(s) / "sharepoint"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{re.sub(r'[^A-Za-z0-9._-]+', '-', item_id)}.bin"
        tmp_path.write_bytes(data if isinstance(data, bytes) else bytes(data or b""))
        text = await asyncio.to_thread(extract_file_text, str(tmp_path))
    except Exception as exc:  # noqa: BLE001
        return f"ERROR extracting text from the SharePoint document: {type(exc).__name__}"

    text = (text or "").strip()
    if not text:
        return f"Downloaded item {item_id} but no readable text could be extracted."
    broadcast_log(manager, f"Ingested SharePoint document {item_id}", level="INFO")
    return f"Ingested SharePoint document {item_id} ({len(text)} chars):\n\n{text[:20000]}"


async def _ado_wiki_session():
    """Return (connector, target) for this session's tenant's ADO wiki, or (None, reason).

    Mirrors _sharepoint_session(): a third read source alongside the repo and
    SharePoint, used to locate the current runbook / knowledge article content.
    """
    s = get_session(get_session_id())
    tenant_id = getattr(s, "tenant_id", "") or ""
    if not tenant_id:
        return None, "ERROR: no tenant context in this session."
    try:
        from shared.services.notification_targets import ado_wiki_target
        from config.connector_factory import get_connector_for_session

        target = await ado_wiki_target(tenant_id)
        if not target:
            return None, (
                "ERROR: Azure DevOps Wiki is not configured for this tenant. An admin "
                "can set ado-wiki-project / ado-wiki-id on the Integrations page, or "
                "use the SharePoint tools instead if the runbook lives there."
            )
        connector = await get_connector_for_session(kind="azure_devops", tenant_id=tenant_id)
        return (connector, target), ""
    except Exception as exc:
        return None, f"ERROR reaching Azure DevOps Wiki: {type(exc).__name__}"


@tool
async def read_wiki_page(page_path: str = "") -> str:
    """Read one page from the project's Azure DevOps wiki (e.g. the current runbook).

    Args:
        page_path: wiki page path, e.g. "/Runbooks/Payments Service". Empty uses the
                   tenant's configured default runbook path.
    """
    resolved, reason = await _ado_wiki_session()
    if not resolved:
        return reason
    connector, target = resolved
    path = page_path or target.get("runbook_path", "/Runbooks")
    try:
        page = await connector.read_adapter(
            "get_wiki_page", project=target.get("project", ""),
            wiki_id=target.get("wiki_id", ""), path=path,
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR reading wiki page {path!r}: {type(exc).__name__}"
    content = (page.get("content") or "").strip()
    if not content:
        return f"Wiki page {path!r} exists but has no content."
    broadcast_log(manager, f"Read wiki page {path}", level="INFO")
    return f"Wiki page {path!r} (version {page.get('version', '?')}):\n\n{content[:20000]}"


@tool
async def list_wiki_pages(path_prefix: str = "") -> str:
    """List page paths under a subtree of the project's Azure DevOps wiki — use this to
    locate the runbook or to search for an existing knowledge article before deciding
    whether to update one or create a new one.

    Args:
        path_prefix: wiki path to list under, e.g. "/Knowledge Base". Empty uses the
                     tenant's configured default knowledge-base path.
    """
    resolved, reason = await _ado_wiki_session()
    if not resolved:
        return reason
    connector, target = resolved
    prefix = path_prefix or target.get("kb_path", "/Knowledge Base")
    try:
        pages = await connector.read_adapter(
            "list_wiki_pages", project=target.get("project", ""),
            wiki_id=target.get("wiki_id", ""), path_prefix=prefix,
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR listing wiki pages under {prefix!r}: {type(exc).__name__}"
    if not pages:
        return f"No wiki pages found under {prefix!r}."
    lines = [f"{len(pages)} page(s) under {prefix!r}:"]
    lines += [f"- {p['path']}" for p in pages[:100]]
    return "\n".join(lines)


@tool
async def diff_markdown_sections(existing_content: str, proposed_content: str) -> str:
    """Compute a real diff between the current document and a proposed update — call
    this instead of hand-writing a diff. Returns JSON with a unified diff and a
    per-section ("## " heading) breakdown of what changed, was added, or was removed.
    Use it to ground a runbook_update or knowledge_article "update" deliverable.
    """
    old_lines = (existing_content or "").splitlines(keepends=True)
    new_lines = (proposed_content or "").splitlines(keepends=True)
    unified = "".join(difflib.unified_diff(
        old_lines, new_lines, fromfile="current", tofile="proposed", lineterm="",
    ))

    def _sections(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        heading, buf = "(preamble)", []
        for line in (text or "").splitlines():
            if line.startswith("## "):
                out[heading] = "\n".join(buf).strip()
                heading, buf = line[3:].strip(), []
            else:
                buf.append(line)
        out[heading] = "\n".join(buf).strip()
        return out

    old_secs, new_secs = _sections(existing_content), _sections(proposed_content)
    sections = []
    for heading in dict.fromkeys(list(old_secs) + list(new_secs)):
        in_old, in_new = heading in old_secs, heading in new_secs
        if in_old and not in_new:
            status = "removed"
        elif in_new and not in_old:
            status = "added"
        elif old_secs.get(heading) != new_secs.get(heading):
            status = "changed"
        else:
            status = "unchanged"
        sections.append({"heading": heading, "status": status})

    return json.dumps({"unified_diff": unified, "sections": sections})


@tool
async def save_runbook_update(
    title: str, filename: str, source_ref: str, unified_diff: str,
    changed_sections_summary: str, updated_sections_markdown: str,
) -> str:
    """Save a RUNBOOK_UPDATE artifact: the diff against the current runbook, which
    sections need updating, and the updated section content. Call diff_markdown_sections
    first to compute unified_diff / changed_sections_summary from real content — do not
    fabricate the diff.

    Args:
        title: human-readable title (e.g. "Runbook update — Payments Service").
        filename: kebab-case filename ending in .md.
        source_ref: where the current runbook was read from (wiki page path or
                    SharePoint item/filename), for traceability.
        unified_diff: the unified diff text from diff_markdown_sections.
        changed_sections_summary: which sections changed/were added/removed, e.g.
                                   "## Rollback steps — changed; ## Escalation — added".
        updated_sections_markdown: the full updated content for each changed section.
    """
    if not updated_sections_markdown or not updated_sections_markdown.strip():
        return "ERROR: refusing to save a runbook update with no updated section content."
    body = (
        f"# {title or 'Runbook Update'}\n\n"
        f"Source: {source_ref or '(unspecified)'}\n\n"
        f"## Sections requiring update\n{changed_sections_summary.strip()}\n\n"
        f"## Diff\n```diff\n{unified_diff.strip()}\n```\n\n"
        f"## Updated section content\n{updated_sections_markdown.strip()}\n"
    )
    result = await save_document.ainvoke({
        "doc_type": "runbook_update", "title": title, "filename": filename,
        "markdown_contents": body,
    })
    if not result.startswith("ERROR"):
        s = get_session(get_session_id())
        if s.generated_docs:
            s.generated_docs[-1]["diff"] = unified_diff
            s.generated_docs[-1]["source_ref"] = source_ref
    return result


@tool
async def save_knowledge_article(
    title: str, filename: str, mode: str, markdown_contents: str,
    issue_ref: str = "", source_ref: str = "",
) -> str:
    """Save a KNOWLEDGE_ARTICLE artifact — either a proposed update to an article that
    already exists for the fixed issue, or a new article from the standard template
    (Symptom / Root cause / Resolution steps / Related links) when none exists. Search
    with list_wiki_pages / list_sharepoint_documents first to decide which mode applies.

    Args:
        mode: "update" (an article already exists for this issue) or "new".
        issue_ref: the fixed issue this article documents (id, title, or PR ref).
        source_ref: the existing article's path/id when mode="update"; empty for "new".
    """
    if mode not in ("update", "new"):
        return "ERROR: mode must be 'update' or 'new'."
    if not markdown_contents or not markdown_contents.strip():
        return "ERROR: refusing to save an empty knowledge article."
    result = await save_document.ainvoke({
        "doc_type": "knowledge_article", "title": title, "filename": filename,
        "markdown_contents": markdown_contents,
    })
    if not result.startswith("ERROR"):
        s = get_session(get_session_id())
        if s.generated_docs:
            s.generated_docs[-1]["issue_ref"] = issue_ref
            s.generated_docs[-1]["source_ref"] = source_ref
    return result


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
