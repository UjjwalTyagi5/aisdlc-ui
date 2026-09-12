"""The project's legacy code: pulled once, read by both Track 3 agents.

WHY ONE PULL PER PROJECT. The Requirements agent (migration intent) asks better
questions once it has seen the code — it can state the current stack instead of asking
for it — and Discovery & Assessment assesses that same code. One read-only checkout per
project serves both, at one commit, so the brief and the assessment describe the same
code. It is pulled from either agent's page (`POST /projects/{id}/modernization/
legacy-code`) or by Discovery's own clone tool, and whichever pulled last wins.

WHERE IT LIVES. `<FILES>/legacy-code/<project_id>/checkout`, with the pull's record
(`pull.json`) beside it. On disk rather than in the database on purpose: the record means
nothing without the checkout next to it, and a column would need a migration that every
other branch's database lacks.

TWO SCOPES. The agent PAGES share the project's checkout (above). An ORCHESTRATOR
conversation is self-contained: code pulled there lives in
`<FILES>/legacy-code/<project_id>/runs/<run_id>/`, is read only by the agents in that
conversation, and never changes what the pages show. `current_scope()` decides from the
turn (the Orchestrator's dispatch marks its turns and binds its run), so every tool here
reads and writes the right copy without being told.

THE RECORD. `status` is the latest ATTEMPT (`pulling` / `ready` / `failed`); `pull` is
the last GOOD pull — what the checkout holds right now. A failed re-pull leaves the
previous checkout and its `pull` in place, so a typo in a URL never takes the code away
from an agent that was using it.

READ-ONLY. The clone is Discovery's `_clone` — shallow, single branch, push URL disabled
— and the tools here only list, read and search. Every path is resolved inside the
checkout; `..`, absolute paths and `.git` are refused.

WHOSE CREDENTIAL. Only the connection the project wired to THE STAGE THE PULL CAME FROM
(the page's agent, or Discovery for its own clone), resolved through
`get_connector_for_session` — so the platform's three connector checks all apply: the
Business Unit must hold the integration grant (Integrations page), the project must
have wired that connection to this stage, and the stage's level must admit `read`.
Deliberately NOT the other Track 3 stage's connection as a fallback: a connection the
project assigned to Discovery is not available to Requirements, and a pull must not be
the way around that. With no permitted connection the clone goes ahead WITHOUT a
credential — which reaches a public repository and nothing else.

WHAT THE AGENTS GET. The whole code cannot go into a model's context (a mid-size legacy
system is hundreds of thousands of tokens), so they get a PROFILE — the same
deterministic inventory Discovery's assessment is built from: modules, languages,
runtimes and whether they are still supported, platform features such as WebForms or
WCF, size — plus tools to read any file they need.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import subprocess
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

#: The two Track 3 stages whose pages can pull, and whose connections may hold the
#: credential for a private repository.
TRACK3_STAGES = ("requirements_modernization", "discovery")

_STALE_PULL = timedelta(minutes=15)
_MAX_READ_BYTES = 2_000_000
_MAX_SEARCH_FILE_BYTES = 1_000_000
_MAX_SEARCH_FILES = 20_000

_LOCKS: dict[str, asyncio.Lock] = {}
#: Background pulls, referenced so the event loop cannot garbage-collect them mid-clone.
_TASKS: set[asyncio.Task] = set()


class PullRefused(ValueError):
    """The request cannot be pulled as asked (bad URL, or a pull already running)."""


# ── where things live ────────────────────────────────────────────────────────


def _root() -> pathlib.Path:
    from config import sdlcSettings  # noqa: PLC0415

    return pathlib.Path(sdlcSettings().FILES) / "legacy-code"


def project_dir(project_id: str, run_id: Optional[str] = None) -> pathlib.Path:
    """The project's copy, or an Orchestrator conversation's own (`run_id`).
    UUIDs and nothing else: both ids become directory names."""
    base = _root() / str(uuid.UUID(str(project_id)))
    return base / "runs" / str(uuid.UUID(str(run_id))) if run_id else base


def checkout_dir(project_id: str, run_id: Optional[str] = None) -> pathlib.Path:
    return project_dir(project_id, run_id) / "checkout"


def _record_file(project_id: str, run_id: Optional[str] = None) -> pathlib.Path:
    return project_dir(project_id, run_id) / "pull.json"


def read_record(project_id: str, run_id: Optional[str] = None) -> Optional[dict]:
    try:
        return json.loads(_record_file(project_id, run_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def _write_record(project_id: str, record: dict, run_id: Optional[str] = None) -> None:
    path = _record_file(project_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def current_pull(project_id: str, run_id: Optional[str] = None) -> Optional[dict]:
    """The last good pull in this scope, when its checkout is actually on disk; else None."""
    record = read_record(project_id, run_id) or {}
    pull = record.get("pull")
    if pull and checkout_dir(project_id, run_id).is_dir():
        return pull
    return None


def current_scope() -> tuple[str, Optional[str]]:
    """(project_id, run_id) whose code this turn reads: an Orchestrator conversation's
    own copy on an Orchestrator turn, the project's (run_id None) on a page's chat."""
    from config.ws_helper import get_orchestrator_run, get_project_id, get_run_id  # noqa: PLC0415

    project_id = str(get_project_id() or "")
    run_id = str(get_run_id() or "") if get_orchestrator_run() else ""
    return project_id, (run_id or None)


def public_record(project_id: str) -> dict:
    """What the pages show. Never the checkout's path on this server."""
    record = read_record(project_id) or {}
    pull = record.get("pull") if checkout_dir(project_id).is_dir() else None
    status = record.get("status") or ("ready" if pull else "none")
    if status == "pulling" and _is_stale(record):
        status = "failed"
    return {
        "status": status,
        "request": record.get("request"),
        "error": record.get("error") or ("The previous pull stopped before it finished — pull again."
                                         if status == "failed" and record.get("status") == "pulling" else ""),
        "pull": pull,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(record: dict) -> bool:
    started = ((record.get("request") or {}).get("startedAt")) or ""
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(started) > _STALE_PULL
    except ValueError:
        return True


def _clean_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    return urllib.parse.urlunparse(parsed._replace(netloc=parsed.hostname or ""))


def _host_provider(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "github.com":
        return "github"
    if host == "dev.azure.com" or host.endswith(".visualstudio.com"):
        return "ado"
    return "public"


def _repo_name(url: str) -> str:
    # Azure DevOps URLs carry spaces as %20 ("Project%202"); the name is shown to people.
    return urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))


# ── the profile ──────────────────────────────────────────────────────────────


def build_profile(root: str | os.PathLike) -> dict:
    """The facts an agent needs before it asks about the current system. Deterministic:
    the inventory Discovery's assessment is built from, without the CVE scan (slow, and
    Discovery's job)."""
    from agents_orchestrator.discovery_agent.analysis.assessment import assess_repository  # noqa: PLC0415

    a = assess_repository(str(root), as_of=date.today())
    s = a["summary"]
    modules = []
    for m in a["modules"]:
        rt = m.get("runtime") or {}
        modules.append({
            "name": m["name"], "path": m["path"], "ecosystem": m["ecosystem"],
            "runtime": " ".join(x for x in (rt.get("name"), rt.get("version")) if x),
            "runtimeStatus": rt.get("status") or "unknown",
            "eolDate": rt.get("eol_date"),
            "loc": m.get("loc", 0), "files": m.get("files", 0),
            "hasTests": bool(m.get("has_tests")),
            "platformFeatures": list(m.get("blockers") or []),
            "dependsOn": list(m.get("depends_on") or []),
            "packageCount": len(m.get("dependencies") or []),
        })
    return {
        "summary": {
            "modules": s.get("module_count", 0), "files": s.get("file_count", 0),
            "loc": s.get("loc", 0), "vendoredFiles": s.get("vendored_files", 0),
            "languages": s.get("languages") or {}, "ecosystems": s.get("ecosystems") or [],
            "endOfLife": (s.get("flag_counts") or {}).get("eol", 0),
            "deprecatedPackages": (s.get("flag_counts") or {}).get("deprecated", 0),
        },
        "modules": modules,
        "endOfLife": [
            {"module": f["module"], "runtime": f["runtime"], "status": f["status"], "eolDate": f.get("eol_date")}
            for f in (a.get("flags") or {}).get("eol") or []
        ],
    }


def profile_markdown(pull: dict) -> str:
    profile = pull.get("profile") or {}
    s = profile.get("summary") or {}
    langs = ", ".join(f"{k} {v:,}" for k, v in sorted((s.get("languages") or {}).items(), key=lambda kv: -kv[1]))
    lines = [
        "# Legacy code pulled for this project",
        "",
        f"Repository: {pull.get('url')} — branch `{pull.get('branch') or 'default'}`, commit "
        f"`{(pull.get('commit') or '')[:12]}`, pulled {pull.get('pulledAt', '')[:16].replace('T', ' ')} UTC.",
        "",
        "## Summary",
        f"- {s.get('modules', 0)} module(s), {s.get('loc', 0):,} lines of code in {s.get('files', 0):,} files"
        + (f" ({s.get('vendoredFiles', 0):,} vendored library files not counted as code)" if s.get("vendoredFiles") else "")
        + ".",
        f"- Languages (lines): {langs or 'none detected'}.",
        f"- Ecosystems: {', '.join(s.get('ecosystems') or []) or 'none detected'}.",
        f"- {s.get('endOfLife', 0)} end-of-life runtime(s); {s.get('deprecatedPackages', 0)} deprecated package(s).",
        "",
        "## Modules",
        "",
        "| Module | Path | Runtime | Support | Platform features | LOC | Tests |",
        "|---|---|---|---|---|---:|---|",
    ]
    for m in (profile.get("modules") or [])[:60]:
        lines.append(
            f"| {m['name']} | `{m['path']}` | {m.get('runtime') or '—'} | {m.get('runtimeStatus')}"
            f"{' (ended ' + m['eolDate'] + ')' if m.get('eolDate') and m.get('runtimeStatus') == 'eol' else ''} | "
            f"{', '.join(m.get('platformFeatures') or []) or '—'} | {m.get('loc', 0):,} | {'yes' if m.get('hasTests') else 'no'} |"
        )
    lines += [
        "",
        "These are facts read from the code: what exists today. They say nothing about WHY the "
        "system is being modernized, what it should become, or what is in scope — ask the user "
        "for those. For detail, use list_legacy_files, read_legacy_file and search_legacy_code.",
    ]
    return "\n".join(lines)


# ── pulling ──────────────────────────────────────────────────────────────────


_STAGE_LABELS = {"requirements_modernization": "Requirements (migration intent)",
                 "discovery": "Discovery & Assessment"}


def stage_may_read() -> bool:
    """Does the connector bound for this call admit `read`? None/unknown/write-only: no."""
    from config.connectors.context import get_connector  # noqa: PLC0415
    from shared.authz.connector_access import permits  # noqa: PLC0415

    try:
        conn = get_connector()
    except Exception:  # noqa: BLE001 — nothing bound
        return False
    return permits(getattr(conn, "access_level", None), "read")


def connection_refusal(stage: str) -> str:
    """Why a stage's connection gave no credential, in words a person can act on."""
    label = _STAGE_LABELS.get(stage, stage)
    return (f"No Azure DevOps or GitHub connection is available to the {label} stage on this "
            "project. The Business Unit needs that integration granted on the Integrations "
            f"page, and a Project Admin wires it to the {label} stage with read access in "
            "project settings. A public repository needs neither.")


async def _connection_secret(url: str, *, tenant_id: str, project_id: str, user_id: str,
                             stage: str) -> tuple[str, str]:
    """(secret, problem) from the connection the project wired to `stage` — and only
    that stage (see WHOSE CREDENTIAL above). Binds the stage's connector the way a turn
    does, for use OUTSIDE a turn (the page's Pull button); inside a turn the connector
    is already bound and `_stage_credentials` is read directly.

    The secret comes back only when the stage's effective access admits `read` AND the
    connection is for the repository's host — a GitHub token is never sent to Azure."""
    from agents_orchestrator.discovery_agent.tools.repo_tools import _stage_credentials  # noqa: PLC0415
    from agents_orchestrator.orchestrator2.connectors import bound_connector  # noqa: PLC0415

    wanted = _host_provider(url)
    async with bound_connector(stage, tenant_id=tenant_id, project_id=project_id, owner_id=user_id) as bound:
        if not bound or not stage_may_read():
            return "", connection_refusal(stage)
        provider, _org, secret, problem = await _stage_credentials()
    if problem:  # permitted, but the credential itself failed — say that, not "no access"
        return "", problem
    if secret and provider == wanted:
        return secret, ""
    if secret:
        return "", (f"The connection wired to the {_STAGE_LABELS.get(stage, stage)} stage is "
                    f"for {'GitHub' if provider == 'github' else 'Azure DevOps'}, not this "
                    "repository's host, so no credential was used.")
    return "", connection_refusal(stage)


async def pull_now(project_id: str, url: str, branch: str, *, user_id: str, secret: str = "",
                   problem: str = "", run_id: Optional[str] = None) -> dict:
    """Clone `url` as the legacy checkout of this scope — the project's, or an
    Orchestrator conversation's (`run_id`) — profile it and record the pull.

    Awaited by the agents' pull tools; run in the background by `start_pull`. Returns
    the record. The previous checkout is only replaced once the new clone succeeded.
    """
    from agents_orchestrator.discovery_agent.tools.repo_tools import _CLONE_TIMEOUT_SECONDS, _clone  # noqa: PLC0415
    from shared.services.ado_repos import _force_rmtree  # noqa: PLC0415

    clean = _clean_url(url)
    lock = _LOCKS.setdefault(f"{project_id}:{run_id or ''}", asyncio.Lock())
    async with lock:
        record = read_record(project_id, run_id) or {}
        request = {"url": clean, "branch": branch, "requestedBy": user_id,
                   "startedAt": (record.get("request") or {}).get("startedAt") if record.get("status") == "pulling" else _now()}
        record.update({"status": "pulling", "request": request, "error": ""})
        _write_record(project_id, record, run_id)

        staging = project_dir(project_id, run_id) / "checkout.new"
        try:
            facts = await asyncio.to_thread(_clone, url, str(staging), branch, secret)
            target = checkout_dir(project_id, run_id)
            if target.exists():
                await asyncio.to_thread(_force_rmtree, target)
            os.replace(staging, target)
            profile = await asyncio.to_thread(build_profile, target)
        except subprocess.TimeoutExpired:
            error = f"Cloning timed out after {_CLONE_TIMEOUT_SECONDS} seconds."
        except Exception as exc:  # noqa: BLE001 — recorded for the page, never raised at it
            logger.warning("legacy code: pull of %s for project %s failed: %s", clean, project_id, exc)
            error = str(exc) or type(exc).__name__
            if not secret:
                error += (" No credential was used — if the repository is private, wire its "
                          "Azure DevOps or GitHub connection to a Code Modernization stage in "
                          "project settings.")
            if problem:
                error += f" Note: {problem}"
        else:
            record.update({
                "status": "ready", "error": "",
                "pull": {
                    "url": clean, "branch": facts["branch"], "commit": facts["commit"],
                    "provider": _host_provider(url), "name": _repo_name(clean),
                    "pulledBy": user_id, "pulledAt": _now(), "profile": profile,
                },
            })
            _write_record(project_id, record, run_id)
            return record
        if staging.exists():
            try:
                await asyncio.to_thread(_force_rmtree, staging)
            except Exception:  # noqa: BLE001
                pass
        record.update({"status": "failed", "error": error[:600]})
        _write_record(project_id, record, run_id)
        return record


async def start_pull(*, tenant_id: str, project_id: str, user_id: str, url: str, branch: str = "",
                     stage: str = TRACK3_STAGES[0]) -> dict:
    """Start a pull in the background and return the page's view of it at once — a
    clone of a real legacy system takes longer than a request should."""
    from agents_orchestrator.discovery_agent.tools.repo_tools import validate_clone_url  # noqa: PLC0415

    url = (url or "").strip()
    refusal = validate_clone_url(url)
    if refusal:
        raise PullRefused(refusal.replace("Discovery may clone from", "can be pulled from"))
    record = read_record(project_id) or {}
    if record.get("status") == "pulling" and not _is_stale(record):
        raise PullRefused("A pull is already running for this project — wait for it to finish.")

    record.update({"status": "pulling", "error": "",
                   "request": {"url": _clean_url(url), "branch": branch, "requestedBy": user_id, "startedAt": _now()}})
    _write_record(project_id, record)

    async def _run() -> None:
        try:
            secret, problem = await _connection_secret(
                url, tenant_id=tenant_id, project_id=project_id, user_id=user_id, stage=stage)
            await pull_now(project_id, url, branch, user_id=user_id, secret=secret, problem=problem)
        except Exception as exc:  # noqa: BLE001
            logger.exception("legacy code: background pull crashed (project %s)", project_id)
            rec = read_record(project_id) or {}
            rec.update({"status": "failed", "error": f"The pull stopped unexpectedly ({type(exc).__name__})."})
            _write_record(project_id, rec)

    task = asyncio.create_task(_run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return public_record(project_id)


# ── the tools ────────────────────────────────────────────────────────────────


def _no_code(project_id: str, run_id: Optional[str] = None) -> str:
    record = read_record(project_id, run_id) if project_id else None
    if record and record.get("status") == "pulling" and not _is_stale(record):
        return "The legacy code is being pulled right now — it will be ready in a minute or two."
    if record and record.get("status") == "failed" and not record.get("pull"):
        return f"The last attempt to pull the legacy code failed: {record.get('error')}"
    if run_id:
        return ("No legacy code has been pulled in this conversation yet. Offer to pull it: the "
                "user can name the repository or give its https URL, or ask you to list what the "
                "project's connection can see.")
    return ("No legacy code has been pulled for this project yet. Offer to pull it here in the "
            "chat (the user names the repository or gives its URL), or the user can press "
            "**Pull legacy code** on this page.")


def _resolve(project_id: str, rel: str, run_id: Optional[str] = None) -> pathlib.Path | str:
    root = checkout_dir(project_id, run_id).resolve()
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if rel.startswith("..") or "/../" in f"/{rel}/" or os.path.isabs(rel):
        return "Paths are relative to the repository root and cannot leave it."
    if rel == ".git" or rel.startswith(".git/"):
        return "The .git folder is not part of the code."
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        return "Paths are relative to the repository root and cannot leave it."
    return target


@tool
async def get_legacy_code_profile() -> str:
    """What the project's pulled legacy code contains: repository and commit, modules,
    languages, runtimes and whether they are still supported, platform features such as
    WebForms or WCF, and size. Call it at the start of a conversation, before asking
    about the current system — and state what it shows instead of asking for it."""
    project_id, run_id = current_scope()
    pull = current_pull(project_id, run_id) if project_id else None
    if not pull:
        return _no_code(project_id, run_id)
    return profile_markdown(pull)


@tool
async def list_legacy_files(path: str = "", max_entries: int = 200) -> str:
    """List the folders and files at `path` in the pulled legacy code (read-only).

    Args:
        path: Relative to the repository root; "" for the root.
        max_entries: At most this many entries (up to 500).
    """
    project_id, run_id = current_scope()
    if not (project_id and current_pull(project_id, run_id)):
        return _no_code(project_id, run_id)
    target = _resolve(project_id, path, run_id)
    if isinstance(target, str):
        return target
    if not target.is_dir():
        return f"'{path}' is not a folder in the legacy code."
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    entries = [e for e in entries if e.name != ".git"][: max(1, min(int(max_entries or 200), 500))]
    if not entries:
        return "Empty folder."
    return "\n".join(f"{e.name}/" if e.is_dir() else f"{e.name}  ({e.stat().st_size:,} bytes)" for e in entries)


@tool
async def read_legacy_file(path: str, start_line: int = 1, max_lines: int = 300) -> str:
    """Read part of a file in the pulled legacy code (read-only), with line numbers.

    Args:
        path: The file, relative to the repository root.
        start_line: First line to return (1-based).
        max_lines: How many lines (up to 800).
    """
    project_id, run_id = current_scope()
    if not (project_id and current_pull(project_id, run_id)):
        return _no_code(project_id, run_id)
    target = _resolve(project_id, path, run_id)
    if isinstance(target, str):
        return target
    if not target.is_file():
        return f"'{path}' is not a file in the legacy code."
    with open(target, "rb") as fh:
        raw = fh.read(_MAX_READ_BYTES + 1)
    if b"\x00" in raw[:8192]:
        return f"'{path}' is a binary file."
    text = raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    count = max(1, min(int(max_lines or 300), 800))
    chunk = lines[start - 1:start - 1 + count]
    if not chunk:
        return f"'{path}' has {len(lines)} line(s); nothing at line {start}."
    body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(chunk))
    more = len(lines) - (start - 1 + len(chunk))
    return body + (f"\n… {more} more line(s) — read on from line {start + len(chunk)}." if more > 0 else "")


@tool
async def search_legacy_code(query: str, path: str = "", max_results: int = 40) -> str:
    """Search the pulled legacy code for text (case-insensitive, plain text — not a
    regex). Returns `file:line: text` matches.

    Args:
        query: The text to look for, e.g. "ServiceContract" or "connectionString".
        path: Optional folder to search in, relative to the repository root.
        max_results: At most this many matches (up to 200).
    """
    from agents_orchestrator.discovery_agent.analysis.inventory import SKIP_DIRS  # noqa: PLC0415

    project_id, run_id = current_scope()
    if not (project_id and current_pull(project_id, run_id)):
        return _no_code(project_id, run_id)
    needle = (query or "").strip().lower()
    if not needle:
        return "Give me some text to search for."
    base = _resolve(project_id, path, run_id)
    if isinstance(base, str):
        return base
    root = checkout_dir(project_id, run_id).resolve()
    limit = max(1, min(int(max_results or 40), 200))

    def _scan() -> list[str]:
        hits: list[str] = []
        seen = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != ".git" and d.lower() not in SKIP_DIRS]
            for name in filenames:
                seen += 1
                if seen > _MAX_SEARCH_FILES:
                    return hits
                full = pathlib.Path(dirpath) / name
                try:
                    if full.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                        continue
                    raw = full.read_bytes()
                except OSError:
                    continue
                if b"\x00" in raw[:8192]:
                    continue
                for n, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    if needle in line.lower():
                        rel = full.relative_to(root).as_posix()
                        hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                        if len(hits) >= limit:
                            return hits
        return hits

    hits = await asyncio.to_thread(_scan)
    if not hits:
        return f"No matches for '{query}'."
    return "\n".join(hits) + (f"\n(first {limit} matches)" if len(hits) >= limit else "")


LEGACY_TOOLS = [get_legacy_code_profile, list_legacy_files, read_legacy_file, search_legacy_code]


def repository_for_brief(project_id: str, run_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The pulled repository (in this scope) in the brief's `legacy_repository` shape, or None."""
    pull = current_pull(project_id, run_id) if project_id else None
    if not pull:
        return None
    return {"url": pull.get("url", ""), "name": pull.get("name", ""), "project": "",
            "provider": pull.get("provider", "")}


# ── pulling from the chat ────────────────────────────────────────────────────

#: Repository name -> clone URL from the last listing, per conversation, so the user can
#: answer "the second one" or "Project 2" rather than paste a URL.
_LISTED: dict[str, dict[str, str]] = {}


def _conversation_key() -> str:
    from config.ws_helper import get_session_id  # noqa: PLC0415

    return str(get_session_id() or "default")


def pull_tools(stage: str) -> list:
    """`find_legacy_repositories` and `pull_legacy_code` for an agent on `stage`.

    The credential is the connection the project wired to THIS stage, bound for the turn
    by the page's socket or the Orchestrator's dispatch — the same three checks as the
    Pull button (Business Unit grant, stage wiring, a level that admits read). Without a
    permitted connection the pull still runs, credential-free: a public repository comes
    through and a private one fails with the reason.
    """
    label = _STAGE_LABELS.get(stage, stage)

    @tool
    async def find_legacy_repositories(ado_project: str = "") -> str:
        """List the repositories the project's Azure DevOps or GitHub connection can see,
        so the user can say which one is the legacy system. Azure DevOps: call with no
        argument to list its projects, then with the chosen project's name. Show the list
        and ask which one — never guess a repository."""
        from agents_orchestrator.discovery_agent.tools.repo_tools import repositories_data  # noqa: PLC0415

        if not stage_may_read():
            return connection_refusal(stage) + " The user can still give a public repository's https URL."
        data = await repositories_data(ado_project.strip())
        if data.get("problem"):
            return data["problem"]
        if data.get("projects") is not None:
            names = data["projects"]
            if not names:
                return "No Azure DevOps projects are visible to this connection."
            return ("Azure DevOps projects:\n" + "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
                    + "\n\nWhich project holds the legacy system? Then list its repositories.")
        repos = data.get("repositories") or []
        if not repos:
            return f"No repositories found{f' in {ado_project}' if ado_project else ''}."
        _LISTED[_conversation_key()] = {r["name"]: r["url"] for r in repos if r.get("url")}
        return ("Repositories:\n" + "\n".join(
            f"{i + 1}. {r['name']}" + (f" (default branch: {r['defaultBranch']})" if r.get("defaultBranch") else "")
            for i, r in enumerate(repos)) + "\n\nWhich one is the legacy system?")

    @tool
    async def pull_legacy_code(repository: str, branch: str = "") -> str:
        """Pull the legacy repository READ-ONLY so you (and, later, Discovery & Assessment)
        can read it. Returns what the code contains.

        Args:
            repository: A name from find_legacy_repositories, or a full https URL.
            branch: Optional; omit for the repository's default branch.
        """
        from agents_orchestrator.discovery_agent.tools.repo_tools import (  # noqa: PLC0415
            _stage_credentials,
            validate_clone_url,
        )
        from config.ws_helper import get_user_id  # noqa: PLC0415

        project_id, run_id = current_scope()
        if not project_id:
            return "This conversation is not attached to a project, so there is nowhere to pull the code to."
        url = (repository or "").strip()
        if not url.lower().startswith("https://"):
            listed = _LISTED.get(_conversation_key(), {})
            url = next((u for n, u in listed.items() if n.lower() == url.lower()), "")
            if not url:
                return (f"I don't know a repository called '{repository}'. List the repositories "
                        "first, or ask the user for the repository's https URL.")
        refusal = validate_clone_url(url)
        if refusal:
            return refusal.replace("Discovery may clone from", "can be pulled from")

        secret, problem = "", ""
        if stage_may_read():
            provider, _org, found, problem = await _stage_credentials()
            if found and provider == _host_provider(url):
                secret = found
        else:
            problem = connection_refusal(stage)

        record = await pull_now(project_id, url, branch.strip(), user_id=str(get_user_id() or ""),
                                secret=secret, problem=problem, run_id=run_id)
        if record.get("status") != "ready":
            return f"The pull failed: {record.get('error') or 'unknown error'}"
        where = "for this conversation" if run_id else "for this project"
        return (f"Pulled read-only {where} using the {label} stage's connection"
                + ("" if secret else " (no credential needed — a public repository)")
                + ".\n\n" + profile_markdown(record["pull"]))

    return [find_legacy_repositories, pull_legacy_code]
