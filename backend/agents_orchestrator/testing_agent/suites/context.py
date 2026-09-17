"""What test case generation reads from a checked-out branch.

Cases are only as good as what they are written from. A functional step that clicks a
button the app does not have, or an API case for a route that does not exist, fails on its
first run and teaches the tester to distrust the suite. So generation is given the code a
tester would read — routes, controllers, views and templates first, then services and
models — and how the app starts, and is told to use only what it was shown.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

SKIP_DIRS = {".git", "node_modules", "dist", "build", "coverage", ".next", "__pycache__", ".venv", "venv",
             "bin", "obj", "target", ".idea", ".vscode", ".pytest_cache", "vendor", "packages"}
SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py", ".cs", ".java", ".go", ".rb", ".php",
              ".ejs", ".hbs", ".handlebars", ".html", ".vue", ".svelte", ".cshtml", ".razor", ".jsp", ".pug"}
#: Path fragments in the order a tester reads them — the surface first.
RANK = (("route", 0), ("controller", 0), ("api", 1), ("view", 1), ("template", 1), ("page", 1),
        ("handler", 1), ("endpoint", 1), ("service", 2), ("model", 2), ("schema", 2), ("app.", 2),
        ("server.", 2), ("index.", 2), ("main.", 2), ("program.", 2), ("startup.", 2))
MAX_FILE_CHARS = 6_000


@dataclass
class RepoContext:
    files: list[str] = field(default_factory=list)          # every source file, repo-relative
    test_files: list[str] = field(default_factory=list)
    text: str = ""                                          # the files shown to the model
    shown: list[str] = field(default_factory=list)
    app_notes: str = ""                                     # stack, start command, port

    def inventory(self) -> str:
        return "\n".join(self.files[:300])


def _is_test(path: str) -> bool:
    p = path.lower()
    return bool(re.search(r"(^|/)(__tests__|tests?|spec)(/|$)|\.(test|spec)\.[a-z]+$|_test\.py$|test_[^/]*\.py$", p))


def _rank(path: str) -> tuple[int, int]:
    p = path.lower()
    return (min((r for frag, r in RANK if frag in p), default=3), len(p))


def app_notes(work_dir: str) -> str:
    """The stack, how the app starts and the port it listens on — from the files, not guessed."""
    notes: list[str] = []
    pkg_path = os.path.join(work_dir, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, encoding="utf-8") as fh:
                pkg = json.load(fh)
            deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
            stack = [n for n in ("express", "ejs", "next", "react", "vue", "koa", "fastify", "nestjs", "jest", "mocha", "vitest", "supertest", "sqlite3", "mongoose", "pg") if n in deps]
            if stack:
                notes.append("Node.js: " + ", ".join(stack))
            scripts = pkg.get("scripts") or {}
            for key in ("start", "dev", "test"):
                if scripts.get(key):
                    notes.append(f"npm run {key} → {scripts[key]}")
        except (OSError, ValueError):
            pass
    for name, label in (("requirements.txt", "Python"), ("pyproject.toml", "Python"), ("pom.xml", "Java (Maven)"), ("go.mod", "Go")):
        if os.path.isfile(os.path.join(work_dir, name)):
            notes.append(label)
    if any(f.endswith(".csproj") for f in os.listdir(work_dir) if os.path.isfile(os.path.join(work_dir, f))):
        notes.append(".NET")

    port = None
    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if os.path.splitext(fn)[1] not in (".js", ".ts", ".mjs", ".cjs", ".py", ".json", ".env", ".cs") and fn != ".env.example":
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8", errors="ignore") as fh:
                    text = fh.read(20_000)
            except OSError:
                continue
            m = re.search(r"(?:PORT|port)\s*(?:\|\||\?\?|=|:)\s*['\"]?(\d{4,5})|listen\(\s*(\d{4,5})", text)
            if m:
                port = m.group(1) or m.group(2)
                break
        if port:
            break
    if port:
        notes.append(f"listens on port {port} by default")
    return "; ".join(notes)


def build_repo_context(work_dir: str, *, max_chars: int = 48_000) -> RepoContext:
    ctx = RepoContext()
    for root, dirs, files in os.walk(work_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() not in SOURCE_EXT:
                continue
            rel = os.path.relpath(os.path.join(root, fn), work_dir).replace("\\", "/")
            (ctx.test_files if _is_test(rel) else ctx.files).append(rel)

    used = 0
    parts: list[str] = []
    for rel in sorted(ctx.files, key=_rank):
        if used >= max_chars:
            break
        try:
            with open(os.path.join(work_dir, rel), encoding="utf-8", errors="ignore") as fh:
                body = fh.read(MAX_FILE_CHARS + 1)
        except OSError:
            continue
        if not body.strip():
            continue
        if len(body) > MAX_FILE_CHARS:
            body = body[:MAX_FILE_CHARS] + "\n… [file continues]"
        body = body[: max_chars - used]
        parts.append(f"=== {rel} ===\n{body}")
        ctx.shown.append(rel)
        used += len(body)
    ctx.text = "\n\n".join(parts)
    ctx.app_notes = app_notes(work_dir)
    return ctx
