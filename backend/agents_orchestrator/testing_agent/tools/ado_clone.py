"""ADO branch cloner â€” Phase B.1.

When the user says "test branch X of repo Y", or selects a branch in the
React UI's repo dropdown, the testing agent invokes `clone_branch()` to
clone the ADO repo into a tmpdir and proceed through the standard
detect_language â†’ analyze â†’ plan â†’ generate â†’ run pipeline.

Security:
- The PAT is embedded in the URL using urllib.parse.quote so a `@`/`#`/`/`
  in the token doesn't break the URL parser.
- subprocess.run uses a list of args (NOT shell=True) so the PAT can't be
  shell-interpreted. The full command-line is NEVER logged â€” only the
  scrubbed form. Stderr is sanitised to remove any echo of the URL.
- Any failure raises RuntimeError with a scrubbed message; no PAT in
  exception text.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional
from urllib.parse import quote

from agents_orchestrator.testing_agent.config.shared import logger


def _scrub(text: str, pat: str) -> str:
    """Replace the PAT with `***` in any string."""
    if not pat:
        return text
    return text.replace(pat, "***").replace(quote(pat), "***")


def _normalise_org_url(org_url: str) -> str:
    """Accept either `https://dev.azure.com/<org>` or just `<org>` â€” return the
    canonical `dev.azure.com/<org>` segment."""
    org_url = (org_url or "").rstrip("/")
    if org_url.startswith("http://"):
        org_url = org_url[len("http://"):]
    if org_url.startswith("https://"):
        org_url = org_url[len("https://"):]
    return org_url


def clone_branch(
    org_url: str,
    project: str,
    repo: str,
    branch: str,
    pat: str,
    work_dir: str,
    *,
    timeout_s: int = 300,
) -> str:
    """Clone a single branch of an ADO repo into work_dir. Returns the path.

    Mirrors agents_orchestrator/development_agent/tools/git_tools._run_git
    (subprocess + list args + capture). NEVER passes shell=True. Logs the
    scrubbed command only.
    """
    if not all((org_url, project, repo, branch, pat, work_dir)):
        raise ValueError("clone_branch: missing required argument")

    os.makedirs(work_dir, exist_ok=True)
    org_seg = _normalise_org_url(org_url)
    safe_pat = quote(pat, safe="")
    url = f"https://anything:{safe_pat}@{org_seg}/{project}/_git/{repo}"

    cmd = [
        "git", "clone",
        "--depth", "1",
        "--branch", branch,
        url,
        work_dir,
    ]
    scrubbed_cmd = [_scrub(arg, pat) for arg in cmd]
    logger.info(f"ado_clone: running {' '.join(scrubbed_cmd[:5])} â€¦ {scrubbed_cmd[-1]}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=os.path.dirname(work_dir) or ".",
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git clone timed out after {timeout_s}s") from exc

    if proc.returncode != 0:
        stderr = _scrub(proc.stderr or "", pat)
        raise RuntimeError(
            f"git clone failed (exit={proc.returncode}): {stderr.strip()[:500]}"
        )

    logger.info(f"ado_clone: cloned {project}/{repo}@{branch} into {work_dir}")
    return work_dir


# Chat-message regex â€” matches "test branch <branch> of repo <repo> in project <project>"
# (canonical form, kept first for backward compatibility with existing callers).
_CHAT_PATTERN = re.compile(
    r"test\s+branch\s+([\w/.\-]+)\s+of\s+repo\s+([\w.\-]+)(?:\s+in\s+project\s+([\w.\-]+))?",
    re.IGNORECASE,
)

# Phase 8.8b â€” additional patterns to handle natural-language phrasings
# the orchestrator-passed conversation_context may contain. Each must
# extract `(branch, repo, project)` in that order via .groups(); project
# may be None. Tried in priority order; first match wins.
_ALT_PATTERNS = (
    # "test branch <branch> in repo <repo> [of project <project>]"
    re.compile(
        r"test\s+branch\s+([\w/.\-]+)\s+in\s+repo\s+([\w.\-]+)(?:\s+(?:of|in)\s+project\s+([\w.\-]+))?",
        re.IGNORECASE,
    ),
    # "test branch <branch> in <repo> [project <project>]" (looser â€” no "repo" keyword)
    re.compile(
        r"test\s+branch\s+([\w/.\-]+)\s+in\s+([\w.\-]+)(?:\s+project\s+([\w.\-]+))?",
        re.IGNORECASE,
    ),
    # "test the <branch> branch in <repo> [repo]" â€” natural English ordering
    re.compile(
        r"test\s+(?:the\s+)?([\w/.\-]+)\s+branch\s+in\s+([\w.\-]+)(?:\s+repo)?",
        re.IGNORECASE,
    ),
    # "test <repo> repo at branch <branch>" â€” captures (repo, branch)
    re.compile(
        r"test\s+([\w.\-]+)\s+repo\s+(?:at\s+)?branch\s+([\w/.\-]+)",
        re.IGNORECASE,
    ),
    # "test <repo>'s <branch> branch" â€” possessive + branch name BEFORE the
    # literal word "branch" (different ordering than the patterns above).
    # Captures (repo, branch).
    re.compile(
        r"test\s+([\w.\-]+)'s\s+([\w/.\-]+)\s+branch\b",
        re.IGNORECASE,
    ),
    # "run tests on branch <branch> of (?:repo|project) <repo>"
    re.compile(
        r"run\s+tests?\s+on\s+branch\s+([\w/.\-]+)\s+(?:of|in)\s+(?:repo|project)\s+([\w.\-]+)",
        re.IGNORECASE,
    ),
)

# Shorthand: "<project>/<repo>@<branch>" â€” matches anywhere in message.
# Both <project> and <repo> are required; branch is required.
_SHORTHAND_PATTERN = re.compile(
    r"\b([\w.\-]+)/([\w.\-]+)@([\w/.\-]+)\b",
)

# Phase 8.10a â€” "repository <repo> in (the )?project <project> (at branch <branch>)?"
# Captures (repo, project, branch?) â€” branch optional, defaults to "main".
# This is the orchestrator's standard rewrite of natural-language testing
# intents (the user's "test basic-calculator" becomes "test repository
# basic-calculator in the project aicore at branch ..."). Without this
# pattern, _LOOSE_PATTERN below was firing first and grabbing the wrong
# capture (project name as repo) because repo and project keywords appear
# in reverse order from the canonical form.
_REPO_PROJECT_PATTERN = re.compile(
    r"repository\s+([\w.\-]+)\s+in\s+(?:the\s+)?project\s+([\w.\-]+)"
    r"(?:\s+(?:at|on)\s+branch\s+([\w/.\-]+))?",
    re.IGNORECASE,
)

# Loose fallback (last resort): "(test|run tests) ... branch X ... (repo|project) Y"
# with arbitrary words in between. Phase 8.10a â€” span between branch and
# the keyword is now bounded ([^\n]{0,40}? not .*?) and DOTALL is dropped so
# the pattern stays on a single line. This prevents the false-positive
# match on multi-noun phrasing like "repository A in the project B at
# branch C", which used to match here and capture the wrong identifier
# (the closer keyword wins under non-greedy lookback).
_LOOSE_PATTERN = re.compile(
    r"(?:test|run\s+tests?)[^\n]{0,80}?branch\s+([\w/.\-]+)[^\n]{0,40}?(?:repo|project)\s+([\w.\-]+)",
    re.IGNORECASE,
)


# Phase 8.9c â€” dev-agent chat-output patterns. The dev agent emits
# user-facing strings like:
#   "Draft PR created: https://dev.azure.com/<org>/<project>/_git/<repo>/pullrequest/<id>"
#   "Repository '<repo>' created in project '<project>'."
#   "Successfully cloned to: <local_path>" (not useful â€” local path only)
#   "Creating branch: <branch>" / "I've committed the following changes on '<branch>'" /
#   "I'll create branch '<branch>' from '<base>'."
# Each pattern below pulls partial hints. Used by extract_dev_chat_hints
# (NOT parse_clone_request â€” single-message clone matches need all three fields,
# but dev hints are typically split across multiple chat messages).

# ADO repo URL (with optional /pullrequest/<id> tail). Captures org, project, repo.
_ADO_URL_PATTERN = re.compile(
    r"https?://dev\.azure\.com/([\w.\-]+)/([\w.\-%]+)/_git/([\w.\-]+)(?:/pullrequest/\d+)?",
    re.IGNORECASE,
)

# "Repository 'X' created in project 'Y'" â€” quoted form
_REPO_CREATED_PATTERN = re.compile(
    r"[Rr]epository\s+['\"]([\w.\-]+)['\"]\s+(?:created|exists?)\s+in\s+project\s+['\"]([\w.\-]+)['\"]",
)

# Phase 10.2 â€” markdown-bold + backtick repo hints. Dev agent emits
# "Cloned **carelon** successfully", "list the repos in the **carelon** project",
# "Repository Selection: ... `carelon`", etc. Captures repo OR project (we
# treat them as the same when only one is mentioned, mirroring the most
# common ADO pattern where project name == repo name).
_REPO_CLONED_PATTERN = re.compile(
    r"[Cc]loned\s+(?:\*\*|`)([\w.\-]+)(?:\*\*|`)\s+(?:successfully|to)?",
)
_REPO_IN_PROJECT_PATTERN = re.compile(
    r"in\s+(?:the\s+)?(?:\*\*|`)([\w.\-]+)(?:\*\*|`)\s+project\b",
    re.IGNORECASE,
)
_REPO_PROJECT_LABEL_PATTERN = re.compile(
    r"(?:^|\n)\s*\*?\*?(?:Project|Repository|Repo)\*?\*?\s*:?\s*(?:\*\*|`)([\w.\-]+)(?:\*\*|`)",
    re.IGNORECASE,
)

# Branch-only hints â€” multiple phrasings the dev LLM may emit.
# Phase 10.2 â€” also accept BACKTICK quoting (markdown), not just single/double quotes.
# Dev's actual output uses `feature/x-y` (backticks) far more than 'feature/x-y'.
_BRANCH_ONLY_PATTERNS = (
    re.compile(r"[Cc]reat(?:ing|ed)\s+branch[:\s]+(?:['\"`])?([\w/.\-]+)(?:['\"`])?"),
    re.compile(r"on\s+branch\s+(?:['\"`])([\w/.\-]+)(?:['\"`])"),
    re.compile(r"branch\s+(?:['\"`])([\w/.\-]+)(?:['\"`])\s+from"),
    re.compile(r"[Cc]hecked\s+out\s+(?:['\"`])?([\w/.\-]+)(?:['\"`])?"),
    re.compile(r"on\s+the\s+(?:['\"`])?([\w/.\-]+)(?:['\"`])?\s+branch", re.IGNORECASE),
    # Dev agent's committed-changes prompt: "I've committed the following changes on '<branch>':"
    re.compile(r"committed.*?on\s+(?:['\"`])([\w/.\-]+)(?:['\"`])", re.IGNORECASE | re.DOTALL),
    # Phase 10.2 â€” "**Branch:** `feature/x-y` is now live on the remote"
    re.compile(
        r"\*?\*?Branch\*?\*?\s*:?\s*`([\w/.\-]+)`",
        re.IGNORECASE,
    ),
    # Orchestrator _format_development_context emits:
    #   ## Branch
    #   feature/my-change
    re.compile(
        r"(?:^|\n)\s*#{1,6}\s*Branch\s*\n\s*`?([\w/.\-]+)`?",
        re.IGNORECASE,
    ),
    # Structured payload text forms: "branch_name: feature/x" or "branch: feature/x".
    re.compile(
        r"\bbranch(?:_name)?\b\s*[:=]\s*`?([\w/.\-]+)`?",
        re.IGNORECASE,
    ),
    # Phase 10.2 â€” "I'll create a new branch `feature/x-y` from `main`"
    re.compile(
        r"new\s+branch\s+`([\w/.\-]+)`",
        re.IGNORECASE,
    ),
    # Phase 10.2 â€” "pushed: feature/x-y" or "Branch pushed successfully"
    # context â€” match standalone backtick branches in feature/* or release/*
    # form when nothing else matched.
    re.compile(r"`((?:feature|release|hotfix|fix|chore|bug)/[\w/.\-]+)`"),
)


def extract_dev_chat_hints(text: str) -> dict:
    """Phase 8.9c â€” pull partial branch/repo/project hints from one dev-agent
    chat message. Returns a dict with whatever subset it could extract;
    fields not found are simply absent.

    Used by the orchestrator-chat-history fallback (Phase 8.9d) when
    `upstream_development` is NULL but the dev agent's user-visible chat
    output has all the info needed to clone. Iterating multiple messages
    newest-first and merging the dicts (later overrides earlier) builds
    the full clone target.

    Returns: dict with any of {"branch", "repo", "project"} present.
    Empty dict on no matches.
    """
    if not text:
        return {}

    hints: dict = {}

    # ADO URL â€” most authoritative for org/project/repo
    m = _ADO_URL_PATTERN.search(text)
    if m:
        # group(1) = org (skipped â€” org comes from the tenant connector)
        hints["project"] = m.group(2)
        hints["repo"] = m.group(3)

    # "Repository 'X' created in project 'Y'" â€” only fill if URL didn't already
    if "repo" not in hints:
        m = _REPO_CREATED_PATTERN.search(text)
        if m:
            hints["repo"] = m.group(1)
            hints["project"] = m.group(2)

    # Phase 10.2 â€” backtick / markdown-bold repo hints
    # "Cloned **carelon** successfully" / "Cloned `carelon` to ..."
    if "repo" not in hints:
        m = _REPO_CLONED_PATTERN.search(text)
        if m:
            hints["repo"] = m.group(1)
            hints.setdefault("project", m.group(1))  # ADO common case: project == repo

    # "in the **carelon** project" / "in `carelon` project"
    if "project" not in hints:
        m = _REPO_IN_PROJECT_PATTERN.search(text)
        if m:
            hints["project"] = m.group(1)
            hints.setdefault("repo", m.group(1))

    # "Project: **carelon**" / "Repository: `carelon`"
    if "repo" not in hints or "project" not in hints:
        m = _REPO_PROJECT_LABEL_PATTERN.search(text)
        if m:
            hints.setdefault("repo", m.group(1))
            hints.setdefault("project", m.group(1))

    # Branch-only hints â€” first match wins (patterns are in priority order)
    for pat in _BRANCH_ONLY_PATTERNS:
        m = pat.search(text)
        if m:
            hints["branch"] = m.group(1)
            break

    return hints


def parse_clone_request(user_message: str) -> Optional[dict]:
    """If the user's chat message is a clone request, return {project, repo, branch}.
    Returns None otherwise.

    Phase 8.8b â€” tries multiple patterns in priority order:
      1. Canonical "test branch X of repo Y [in project Z]" (existing behavior)
      2. "test branch X in repo Y [of project Z]" / "test branch X in Y"
      3. "test the X branch in Y [repo]"
      4. "test Y('s | repo) at branch X"
      5. "run tests on branch X of (repo|project) Y"
      6. Shorthand "Z/Y@X" matched anywhere
      7. Loose fallback "(test|run tests) ... branch X ... (repo|project) Y"
    """
    if not user_message:
        return None

    # 1. Canonical form (kept first for backward compatibility)
    m = _CHAT_PATTERN.search(user_message)
    if m:
        branch, repo, project = m.group(1), m.group(2), m.group(3)
        return {"branch": branch, "repo": repo, "project": project}

    # 2-N. Alternative patterns â€” each captures (branch, repo, project?) by
    # default. Patterns at indices 3 and 4 are exceptions: they capture
    # (repo, branch) because the natural English phrasing puts repo first
    # ("test <repo> repo at branch <branch>" / "test <repo>'s <branch> branch").
    _REPO_FIRST_INDICES = {3, 4}
    for i, pat in enumerate(_ALT_PATTERNS):
        m = pat.search(user_message)
        if m:
            groups = m.groups()
            if i in _REPO_FIRST_INDICES:
                repo, branch = groups[0], groups[1]
                return {"branch": branch, "repo": repo, "project": None}
            branch, repo = groups[0], groups[1]
            project = groups[2] if len(groups) > 2 else None
            return {"branch": branch, "repo": repo, "project": project}

    # 6. Shorthand "<project>/<repo>@<branch>"
    m = _SHORTHAND_PATTERN.search(user_message)
    if m:
        project, repo, branch = m.group(1), m.group(2), m.group(3)
        return {"branch": branch, "repo": repo, "project": project}

    # 7. Phase 8.10a â€” "repository <repo> in (the )?project <project> (at branch <branch>)?"
    # Tried BEFORE the loose fallback so it claims the (repo, project) order
    # correctly. Without this, the loose pattern grabs project name as repo.
    m = _REPO_PROJECT_PATTERN.search(user_message)
    if m:
        repo, project, branch = m.group(1), m.group(2), m.group(3) or "main"
        return {"branch": branch, "repo": repo, "project": project}

    # 8. Loose fallback â€” handles "(test|run tests) ... branch X ... repo Y"
    # with arbitrary words between. Restrictive enough to avoid false-
    # positives on innocent messages like "test the new design".
    m = _LOOSE_PATTERN.search(user_message)
    if m:
        branch, repo = m.group(1), m.group(2)
        return {"branch": branch, "repo": repo, "project": None}

    return None
