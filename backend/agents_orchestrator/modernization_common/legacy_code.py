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


def project_dir(project_id: str) -> pathlib.Path:
    # A UUID and nothing else: the id becomes a directory name.
    return _root() / str(uuid.UUID(str(project_id)))


def checkout_dir(project_id: str) -> pathlib.Path:
    return project_dir(project_id) / "checkout"


def _record_file(project_id: str) -> pathlib.Path:
    return project_dir(project_id) / "pull.json"


def read_record(project_id: str) -> Optional[dict]:
    try:
        return json.loads(_record_file(project_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def _write_record(project_id: str, record: dict) -> None:
    path = _record_file(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def current_pull(project_id: str) -> Optional[dict]:
    """The last good pull, when its checkout is actually on disk; else None."""
    record = read_record(project_id) or {}
    pull = record.get("pull")
    if pull and checkout_dir(project_id).is_dir():
        return pull
    return None


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


_STAGE_LABELS = {"requirements_modernization": "Migration Intent",
                 "discovery": "Dependency and Risk"}


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
                   problem: str = "") -> dict:
    """Clone `url` as the project's legacy checkout, profile it and record the pull.

    Awaited by Discovery's clone tool; run in the background by `start_pull`. Returns
    the record. The previous checkout is only replaced once the new clone succeeded.
    """
    from agents_orchestrator.discovery_agent.tools.repo_tools import _CLONE_TIMEOUT_SECONDS, _clone  # noqa: PLC0415
    from shared.services.ado_repos import _force_rmtree  # noqa: PLC0415

    clean = _clean_url(url)
    lock = _LOCKS.setdefault(str(project_id), asyncio.Lock())
    async with lock:
        record = read_record(project_id) or {}
        request = {"url": clean, "branch": branch, "requestedBy": user_id,
                   "startedAt": (record.get("request") or {}).get("startedAt") if record.get("status") == "pulling" else _now()}
        record.update({"status": "pulling", "request": request, "error": ""})
        _write_record(project_id, record)

        staging = project_dir(project_id) / "checkout.new"
        try:
            facts = await asyncio.to_thread(_clone, url, str(staging), branch, secret)
            target = checkout_dir(project_id)
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
            _write_record(project_id, record)
            return record
        if staging.exists():
            try:
                await asyncio.to_thread(_force_rmtree, staging)
            except Exception:  # noqa: BLE001
                pass
        record.update({"status": "failed", "error": error[:600]})
        _write_record(project_id, record)
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


def _current_project() -> str:
    from config.ws_helper import get_project_id  # noqa: PLC0415

    return str(get_project_id() or "")


def _no_code(project_id: str) -> str:
    record = read_record(project_id) if project_id else None
    if record and record.get("status") == "pulling" and not _is_stale(record):
        return "The legacy code is being pulled right now — it will be ready in a minute or two."
    if record and record.get("status") == "failed" and not record.get("pull"):
        return f"The last attempt to pull the legacy code failed: {record.get('error')}"
    return ("No legacy code has been pulled for this project yet. Ask the user to press "
            "**Pull legacy code** on the agent's page and choose the repository.")


def _resolve(project_id: str, rel: str) -> pathlib.Path | str:
    root = checkout_dir(project_id).resolve()
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
    project_id = _current_project()
    pull = current_pull(project_id) if project_id else None
    if not pull:
        return _no_code(project_id)
    return profile_markdown(pull)


@tool
async def list_legacy_files(path: str = "", max_entries: int = 200) -> str:
    """List the folders and files at `path` in the pulled legacy code (read-only).

    Args:
        path: Relative to the repository root; "" for the root.
        max_entries: At most this many entries (up to 500).
    """
    project_id = _current_project()
    if not (project_id and current_pull(project_id)):
        return _no_code(project_id)
    target = _resolve(project_id, path)
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
    project_id = _current_project()
    if not (project_id and current_pull(project_id)):
        return _no_code(project_id)
    target = _resolve(project_id, path)
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

    project_id = _current_project()
    if not (project_id and current_pull(project_id)):
        return _no_code(project_id)
    needle = (query or "").strip().lower()
    if not needle:
        return "Give me some text to search for."
    base = _resolve(project_id, path)
    if isinstance(base, str):
        return base
    root = checkout_dir(project_id).resolve()
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


def repository_for_brief(project_id: str) -> Optional[dict[str, Any]]:
    """The pulled repository in the brief's `legacy_repository` shape, or None."""
    pull = current_pull(project_id) if project_id else None
    if not pull:
        return None
    return {"url": pull.get("url", ""), "name": pull.get("name", ""), "project": "",
            "provider": pull.get("provider", "")}
