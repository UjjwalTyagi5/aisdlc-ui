"""What is on a branch: every tracked file, its size, lines and language.

The Code Review agent's WHOLE-BRANCH target has no diff to scope a review by. This is
the scope instead — the list the agent plans its reading from, the page's Files tab,
and the report's "what was reviewed" section, which compares it with the files the
agent actually opened so coverage is stated, not implied.

Tracked files only (`git ls-files`): an untracked build artefact in the clone is not
part of the branch.
"""
from __future__ import annotations

import pathlib
import subprocess

_LANGUAGES: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".cs": "C#",
    ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rb": "Ruby", ".php": "PHP",
    ".rs": "Rust", ".swift": "Swift", ".scala": "Scala", ".sql": "SQL", ".sh": "Shell",
    ".ps1": "PowerShell", ".html": "HTML", ".ejs": "EJS template", ".hbs": "Handlebars",
    ".css": "CSS", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML",
    ".md": "Markdown", ".tf": "Terraform", ".bicep": "Bicep", ".dockerfile": "Dockerfile",
    ".csproj": "MSBuild", ".gradle": "Gradle",
}
_NAMED: dict[str, str] = {"Dockerfile": "Dockerfile", "Makefile": "Makefile", "Jenkinsfile": "Groovy"}
#: Files that carry no code to review: placeholders and lockfiles.
_NOT_CODE = {".gitkeep", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}
_MAX_READ = 2_000_000


def language_of(path: str) -> str:
    name = pathlib.PurePosixPath(path).name
    if name in _NAMED:
        return _NAMED[name]
    return _LANGUAGES.get(pathlib.PurePosixPath(path).suffix.lower(), "Other")


def branch_inventory(work_dir: str) -> dict:
    """{files: [{path, bytes, lines, language, reviewable}], totals: {...}, languages: {lang: files}}.

    Raises RuntimeError when git cannot list the branch — an empty list must mean an
    empty branch, never a failed command.
    """
    root = pathlib.Path(work_dir)
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(root), capture_output=True, timeout=60,
    )
    if out.returncode != 0:
        detail = (out.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"Listing the branch's files failed: {detail or f'git exit {out.returncode}'}")

    files: list[dict] = []
    for raw in out.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace")
        fp = root / rel
        try:
            size = fp.stat().st_size
        except OSError:
            continue
        data = b""
        if size <= _MAX_READ:
            try:
                data = fp.read_bytes()
            except OSError:
                data = b""
        binary = b"\0" in data[:8000]
        lines = 0 if binary else data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        name = pathlib.PurePosixPath(rel).name
        files.append({
            "path": rel,
            "bytes": size,
            "lines": lines,
            "language": "Binary" if binary else language_of(rel),
            "reviewable": not binary and size > 0 and name not in _NOT_CODE,
        })

    files.sort(key=lambda f: f["path"])
    languages: dict[str, int] = {}
    for f in files:
        if f["reviewable"]:
            languages[f["language"]] = languages.get(f["language"], 0) + 1
    reviewable = [f for f in files if f["reviewable"]]
    return {
        "files": files,
        "languages": dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        "totals": {
            "files": len(files),
            "reviewable_files": len(reviewable),
            "lines": sum(f["lines"] for f in reviewable),
            "bytes": sum(f["bytes"] for f in files),
        },
    }
