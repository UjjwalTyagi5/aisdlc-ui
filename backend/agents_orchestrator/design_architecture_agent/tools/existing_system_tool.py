"""Existing-system analysis tool for the Design Agent (reference §4.2 Step 2).

Read-only introspection of a local repository path: detects frameworks (from manifest
files), the language mix (by file extension), and likely API / DB-model source files.
No git clone and no network — it operates on a path already present on disk. Degrades
gracefully when the path is absent or unreadable; never raises.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".go": "go", ".rb": "ruby", ".cs": "csharp", ".php": "php",
    ".rs": "rust", ".kt": "kotlin",
}
# (substring in manifest/source filename or its text) -> framework label
_MANIFEST_FRAMEWORKS = {
    "fastapi": "fastapi", "flask": "flask", "django": "django", "express": "express",
    "react": "react", "next": "nextjs", "spring-boot": "spring", "springframework": "spring",
    "sqlalchemy": "sqlalchemy", "prisma": "prisma", "gin-gonic": "gin",
}
_MANIFEST_FILES = {"requirements.txt", "pyproject.toml", "package.json", "pom.xml",
                   "build.gradle", "go.mod", "Gemfile", "composer.json"}
_API_HINTS = ("router", "routes", "controller", "api", "endpoint", "urls")
_DB_HINTS = ("model", "models", "schema", "entity", "migration", "repository")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
_MAX_FILES = 4000


@tool
async def analyze_existing_system(repo_path: str) -> str:
    """Analyse an existing codebase on disk before designing, so the design extends it.

    Detects frameworks, language mix, and likely API/DB source files via a read-only
    directory walk. Use EARLY (reference Step 2) when a repo path is available, so the
    HLD/LLD reuse existing patterns instead of inventing a greenfield stack.

    Args:
        repo_path: Absolute path to a local checkout of the existing system.

    Returns:
        JSON string summarising the existing system, or an unavailable message.
    """
    root = Path(repo_path)
    if not root.exists() or not root.is_dir():
        return json.dumps({
            "status": "unavailable",
            "message": f"Repo path not found or not a directory: {repo_path}",
            "frameworks": [], "languages": {}, "api_files": [], "db_files": [], "summary": "",
        })

    frameworks: set[str] = set()
    languages: Counter = Counter()
    api_files: list[str] = []
    db_files: list[str] = []
    seen = 0

    try:
        for dirpath, dirnames, filenames in os.walk(str(root)):
            if seen >= _MAX_FILES:
                break
            # Prune skip-dirs in-place so os.walk never descends into them
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in filenames:
                if seen >= _MAX_FILES:
                    break
                try:
                    full = os.path.join(dirpath, filename)
                    path = Path(full)
                    if not path.is_file():
                        continue
                    seen += 1
                    name = filename.lower()
                    rel = str(path.relative_to(root))

                    ext = path.suffix.lower()
                    if ext in _EXT_LANG:
                        languages[_EXT_LANG[ext]] += 1

                    if name in _MANIFEST_FILES:
                        try:
                            text = path.read_text(encoding="utf-8", errors="ignore").lower()
                        except Exception:
                            text = ""
                        for needle, label in _MANIFEST_FRAMEWORKS.items():
                            if needle in text:
                                frameworks.add(label)

                    stem = name.rsplit(".", 1)[0]
                    if any(h in stem for h in _API_HINTS):
                        api_files.append(rel)
                    if any(h in stem for h in _DB_HINTS):
                        db_files.append(rel)
                except (OSError, PermissionError):
                    continue
    except Exception as e:
        return json.dumps({
            "status": "error", "message": f"Walk failed: {str(e)[:200]}",
            "frameworks": [], "languages": {}, "api_files": [], "db_files": [], "summary": "",
        })

    top_lang = languages.most_common(1)[0][0] if languages else "unknown"
    summary = (
        f"Scanned {seen} files. Primary language: {top_lang}. "
        f"Frameworks: {', '.join(sorted(frameworks)) or 'none detected'}. "
        f"{len(api_files)} likely API file(s), {len(db_files)} likely data-model file(s)."
    )
    return json.dumps({
        "status": "ok",
        "frameworks": sorted(frameworks),
        "languages": dict(languages),
        "api_files": api_files[:50],
        "db_files": db_files[:50],
        "summary": summary,
    })
