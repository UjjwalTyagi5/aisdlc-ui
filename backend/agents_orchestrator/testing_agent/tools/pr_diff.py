"""PR-diff helpers — Phase 7.

Compute the set of files + line ranges a PR changed, so testing-agent can:
1. Focus the LLM test-plan prompt on changed code only (smaller prompt → no
   more empty TestPlans on large repos like carelon).
2. Compute PR-scoped coverage (% of changed lines actually exercised by
   tests) — far more meaningful than whole-repo % when the repo is big and
   the PR is small.

All helpers are best-effort: on any error (no merge base, shallow clone,
no base branch present), they return empty / falsy values so the caller
falls back to whole-repo behaviour.
"""
from __future__ import annotations

import re
import subprocess
from typing import Dict, List, Set


def _run_git(args: List[str], cwd: str, timeout_s: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)


def detect_default_branch(work_dir: str) -> str:
    """Return the repo's default branch (main / master / dev). Falls back to 'main'."""
    rc, out, _ = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], work_dir)
    if rc == 0 and "/" in out:
        return out.strip().rsplit("/", 1)[-1]
    for candidate in ("main", "master", "develop"):
        rc, _, _ = _run_git(["rev-parse", f"origin/{candidate}"], work_dir)
        if rc == 0:
            return candidate
    return "main"


def fetch_base_branch(work_dir: str, base: str) -> bool:
    """Make sure the base branch is locally available so we can diff against it.

    Shallow clones (`git clone --depth 1`) only have the PR branch — without
    fetching the base, `git diff` can't find a merge base. Returns True if
    the base ref is now available.
    """
    rc, _, _ = _run_git(["rev-parse", f"origin/{base}"], work_dir)
    if rc == 0:
        return True
    # Try fetching with deepening so we have history for a merge-base
    rc, _, _ = _run_git(
        ["fetch", "--depth=200", "origin", f"{base}:refs/remotes/origin/{base}"],
        work_dir, timeout_s=120,
    )
    if rc == 0:
        return True
    # Last-resort: unshallow entirely (slow but always works)
    rc, _, _ = _run_git(["fetch", "--unshallow", "origin", base], work_dir, timeout_s=180)
    return rc == 0


def changed_files(work_dir: str, base: str) -> List[str]:
    """List files changed in current branch vs `origin/<base>`. Empty on failure.

    Phase 8.2 — try the symmetric form first (uses merge-base, more accurate:
    reports renames as renames, ignores changes on the base branch since the
    fork point), fall back to two-arg form for shallow clones where no
    merge-base is available.
    """
    # Try merge-base form first — semantically correct for PR diffs
    rc, out, _ = _run_git(["diff", "--name-only", f"origin/{base}...HEAD"], work_dir)
    if rc == 0:
        return [f for f in out.strip().split("\n") if f]
    # Fall back to two-arg form (works on shallow clones — no merge-base needed)
    rc, out, _ = _run_git(["diff", "--name-only", f"origin/{base}", "HEAD"], work_dir)
    if rc != 0:
        return []
    return [f for f in out.strip().split("\n") if f]


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_line_ranges(work_dir: str, base: str) -> Dict[str, Set[int]]:
    """Return {file_path: {line_numbers...}} for ADDED/modified lines vs base.

    Uses `git diff --unified=0` so each hunk's `@@ +start,len @@` header gives
    us exact line numbers without context lines noise.
    """
    # Phase 8.2 — try merge-base form first (more accurate), fall back to
    # two-arg form on shallow clones.
    rc, out, _ = _run_git(
        ["diff", "--unified=0", f"origin/{base}...HEAD"],
        work_dir, timeout_s=60,
    )
    if rc != 0:
        rc, out, _ = _run_git(
            ["diff", "--unified=0", f"origin/{base}", "HEAD"],
            work_dir, timeout_s=60,
        )
    if rc != 0:
        return {}
    result: Dict[str, Set[int]] = {}
    current_file: str | None = None
    for line in out.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            result.setdefault(current_file, set())
            continue
        m = _HUNK_RE.match(line)
        if m and current_file:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) else 1
            if length > 0:  # length 0 means deletion-only, no new lines
                for ln in range(start, start + length):
                    result[current_file].add(ln)
    return result
