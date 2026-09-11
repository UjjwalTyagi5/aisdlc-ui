"""What is in the legacy repository: its modules, languages, size, tests and blockers.

A MODULE IS A DIRECTORY WITH A PROJECT MANIFEST — a `.csproj`, a `pom.xml`, a
`package.json`, a `pyproject.toml`. That is the unit a migration moves: it builds on
its own, it has its own dependencies, and it is what a wave in Strategy will list. A
repository with no manifest anywhere is treated as a single module rooted at the top.

Every file belongs to the DEEPEST module that contains it, so a test project nested
under a solution folder is its own module rather than inflating its parent.

Vendored and generated trees (`node_modules`, `bin`, `obj`, NuGet's `packages`
cache, ...) are never counted: they are not the application's code, and counting them
would make every JavaScript project look enormous and every .NET one look tested.

PURE AND DETERMINISTIC. No network, no subprocess, no model. The risk score built on
top of this has to come out the same on every run for the same commit.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Directory names that are never the application's own code.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".vs", ".idea", ".vscode", ".gradle", ".next", ".nuxt",
    "node_modules", "bower_components", "bin", "obj", "dist", "build", "target", "out",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache", "coverage",
    "vendor", "TestResults",
})

#: Manifest file → ecosystem. Order matters when one directory holds two manifests
#: (an ASP.NET project with a package.json for its front-end assets is a .NET module).
_MANIFEST_PRIORITY: tuple[tuple[str, str], ...] = (
    (".csproj", ".NET"),
    (".vbproj", ".NET"),
    (".fsproj", ".NET"),
    ("pom.xml", "Java"),
    ("build.gradle", "Java"),
    ("build.gradle.kts", "Java"),
    ("package.json", "Node.js"),
    ("pyproject.toml", "Python"),
    ("setup.py", "Python"),
    ("requirements.txt", "Python"),
    ("go.mod", "Go"),
    ("composer.json", "PHP"),
    ("Gemfile", "Ruby"),
)

#: Extension → language, for the source files whose lines count as code.
LANGUAGE_BY_EXT: dict[str, str] = {
    ".cs": "C#", ".vb": "VB.NET", ".fs": "F#",
    ".aspx": "ASP.NET WebForms", ".ascx": "ASP.NET WebForms", ".master": "ASP.NET WebForms",
    ".cshtml": "Razor", ".vbhtml": "Razor",
    ".java": "Java", ".kt": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".py": "Python", ".go": "Go", ".rb": "Ruby", ".php": "PHP",
    ".sql": "SQL", ".cbl": "COBOL", ".cob": "COBOL",
}

#: Files larger than this are counted as files but not read for lines — a generated
#: bundle or a data dump says nothing about migration effort and costs seconds to read.
_MAX_READ_BYTES = 2_000_000

#: Test-file conventions. Script languages are matched case-insensitively; the typed
#: languages use the PascalCase `Tests` suffix convention, case-SENSITIVELY, because a
#: lowercase match would count `Contest.cs` and `Latest.java` as tests.
_TEST_SCRIPT_RE = re.compile(
    r"(?i)^(?:test_.*\.py|.*_test\.(?:py|go)|.*\.(?:test|spec)\.[cm]?[jt]sx?)$"
)
_TEST_TYPED_RE = re.compile(r"^(?:.*Tests?|Test[A-Z].*)\.(?:cs|vb|fs|java|kt)$")
_TEST_MODULE_RE = re.compile(r"(?i)(?:^|[._-])(?:unit|integration|functional)?tests?$")

#: THIRD-PARTY FRONT-END CODE CHECKED INTO THE APPLICATION. Found on a real legacy
#: repository (eShopModernizing): every ASP.NET app carried ~40k lines of jQuery,
#: jQuery-slim and Bootstrap under `Scripts/`, so each web module maxed out the size
#: factor on code nobody migrates — it is replaced by a package reference, not ported.
#: Such a file still counts as a FILE (it is there, and it must be dealt with) but not
#: as lines of code. Only script and stylesheet files are ever judged vendored.
_VENDORABLE_EXT = frozenset({".js", ".mjs", ".cjs", ".css"})
_GENERATED_ASSET_RE = re.compile(r"(?i)\.(?:min|slim|bundle)\.(?:js|mjs|cjs|css)$|\.d\.ts$")
_VENDOR_LIBRARY_RE = re.compile(
    r"(?i)^(?:jquery|bootstrap|modernizr|respond|popper|knockout|angular|react(?:-dom)?|vue|"
    r"lodash|underscore|moment|globalize|microsoftajax|microsoftmvc|_references)\b"
)
_VENDOR_DIR_RE = re.compile(
    r"(?i)(?:^|/)(?:wwwroot/lib|scripts/webforms|vendor|vendors|third_party|thirdparty)(?:/|$)"
)


def is_vendored_asset(rel_dir: str, name: str) -> bool:
    """Is this file third-party or generated front-end code rather than the app's own?"""
    if name.lower().endswith(".d.ts"):
        return True
    if Path(name).suffix.lower() not in _VENDORABLE_EXT:
        return False
    return bool(
        _GENERATED_ASSET_RE.search(name)
        or _VENDOR_LIBRARY_RE.match(name)
        or _VENDOR_DIR_RE.search(rel_dir.replace("\\", "/"))
    )

#: Platform blockers: APIs with no drop-in equivalent on modern cross-platform runtimes.
#: The HARD ones (a rewrite, not a port) force the manual tier in `risk.py`.
HARD_BLOCKERS = frozenset({"webforms", "wcf_server", "remoting"})
_MANIFEST_BLOCKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("winforms", re.compile(r"System\.Windows\.Forms|<UseWindowsForms>\s*true", re.I)),
    ("wpf", re.compile(r"PresentationFramework|<UseWPF>\s*true", re.I)),
    ("remoting", re.compile(r"System\.Runtime\.Remoting", re.I)),
    ("system_web", re.compile(r"Include=\"System\.Web(?:\.Mvc)?\"", re.I)),
)


@dataclass
class ModuleFacts:
    """One module as the file system shows it — before any manifest is parsed."""

    name: str
    path: str          # posix, relative to the repository root ("." for the root)
    ecosystem: str
    manifest: str      # posix path of the manifest file, relative to the root
    files: int = 0
    loc: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    has_tests: bool = False
    blockers: list[str] = field(default_factory=list)
    #: Third-party/generated front-end files: counted in `files`, not in `loc`.
    vendored_files: int = 0


@dataclass
class Inventory:
    root: str
    modules: list[ModuleFacts]
    file_count: int
    loc: int
    languages: dict[str, int]


def _is_nuget_cache(path: Path) -> bool:
    """NuGet's solution-level `packages/` folder — but not a monorepo's `packages/`
    of workspace modules, which is where many JavaScript repositories keep their code."""
    if path.name != "packages":
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and any(p.suffix == ".nupkg" for p in child.iterdir()):
                return True
    except OSError:
        return False
    return False


def _walk(root: Path):
    """(dir, filenames) for every directory that is the application's own code."""
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not _is_nuget_cache(here / d)
        )
        yield here, sorted(filenames)


def _manifest_in(filenames: list[str]) -> tuple[str, str] | None:
    for suffix, ecosystem in _MANIFEST_PRIORITY:
        for name in filenames:
            if name == suffix or (suffix.startswith(".") and name.endswith(suffix)):
                return name, ecosystem
    return None


def _rel(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel or "."


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > _MAX_READ_BYTES:
            return 0
        with path.open("rb") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _module_name(root: Path, directory: Path, manifest: str, ecosystem: str) -> str:
    """The name people call this module: the project file's name for .NET, the
    declared package/artifact name elsewhere, the directory otherwise."""
    if ecosystem == ".NET":
        return Path(manifest).stem
    text = _read_text(directory / manifest)
    if manifest == "package.json":
        match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1)
    if manifest == "pom.xml":
        # The project's own artifactId is the first one outside <parent>.
        without_parent = re.sub(r"(?s)<parent>.*?</parent>", "", text)
        match = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", without_parent)
        if match:
            return match.group(1)
    if manifest == "pyproject.toml":
        match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
        if match:
            return match.group(1)
    return directory.name if directory != root else root.name


def _blockers(directory: Path, manifest: str, ecosystem: str, extensions: set[str]) -> list[str]:
    found: list[str] = []
    if extensions & {".aspx", ".ascx", ".master"}:
        found.append("webforms")
    if ".svc" in extensions:
        found.append("wcf_server")
    if ecosystem == ".NET":
        text = _read_text(directory / manifest)
        for name, pattern in _MANIFEST_BLOCKER_PATTERNS:
            if name == "system_web" and "webforms" in found:
                continue  # WebForms already says it, and louder
            if pattern.search(text) and name not in found:
                found.append(name)
    return found


def scan_inventory(root: Path) -> Inventory:
    """Walk `root` and describe its modules. Never raises for a readable directory."""
    root = Path(root)
    module_dirs: dict[str, tuple[str, str]] = {}  # rel dir -> (manifest, ecosystem)
    files_by_dir: dict[str, list[str]] = {}
    for directory, filenames in _walk(root):
        rel = _rel(root, directory)
        files_by_dir[rel] = filenames
        found = _manifest_in(filenames)
        if found:
            module_dirs[rel] = found

    if not module_dirs:
        # No manifest anywhere: the whole repository is one module of unknown build.
        module_dirs["."] = ("", "unknown")

    # Deepest-first, so a file is attributed to the innermost module containing it.
    ordered = sorted(module_dirs, key=lambda p: (p.count("/") if p != "." else -1), reverse=True)

    facts: dict[str, ModuleFacts] = {}
    extensions: dict[str, set[str]] = {p: set() for p in module_dirs}
    test_files: dict[str, bool] = {p: False for p in module_dirs}
    for rel, (manifest, ecosystem) in module_dirs.items():
        directory = root if rel == "." else root / rel
        facts[rel] = ModuleFacts(
            name=_module_name(root, directory, manifest, ecosystem) if manifest else root.name,
            path=rel,
            ecosystem=ecosystem,
            manifest=(f"{rel}/{manifest}" if rel != "." else manifest) if manifest else "",
        )

    total_files = 0
    total_loc = 0
    languages: dict[str, int] = {}
    for rel_dir, filenames in files_by_dir.items():
        owner = next(
            (m for m in ordered if m == "." or rel_dir == m or rel_dir.startswith(m + "/")),
            None,
        )
        for name in filenames:
            total_files += 1
            if owner is None:
                continue
            path = (root if rel_dir == "." else root / rel_dir) / name
            ext = path.suffix.lower()
            module = facts[owner]
            module.files += 1
            if is_vendored_asset(rel_dir, name):
                module.vendored_files += 1
                continue  # a file, not code: no lines, no language, no test signal
            extensions[owner].add(ext)
            if (
                _TEST_SCRIPT_RE.match(name)
                or _TEST_TYPED_RE.match(name)
                or "/__tests__" in f"/{rel_dir}"
                or "/src/test/" in f"/{rel_dir}/"
            ):
                test_files[owner] = True
            language = LANGUAGE_BY_EXT.get(ext)
            if language:
                lines = _count_lines(path)
                module.languages[language] = module.languages.get(language, 0) + lines
                module.loc += lines
                languages[language] = languages.get(language, 0) + lines
                total_loc += lines

    for rel, module in facts.items():
        directory = root if rel == "." else root / rel
        module.has_tests = test_files[rel] or bool(_TEST_MODULE_RE.search(module.name))
        if module.manifest:
            module.blockers = _blockers(
                directory, Path(module.manifest).name, module.ecosystem, extensions[rel]
            )
        else:
            module.blockers = _blockers(directory, "", module.ecosystem, extensions[rel])

    _dedupe_names(facts.values())
    modules = sorted(facts.values(), key=lambda m: m.path)
    return Inventory(
        root=str(root), modules=modules, file_count=total_files, loc=total_loc,
        languages=dict(sorted(languages.items(), key=lambda kv: -kv[1])),
    )


def _dedupe_names(modules) -> None:
    """Two modules may declare the same name (two `package.json` called "app").
    Names are identifiers everywhere downstream, so a clash is disambiguated by path."""
    seen: dict[str, int] = {}
    for module in modules:
        seen[module.name] = seen.get(module.name, 0) + 1
    for module in modules:
        if seen[module.name] > 1:
            module.name = f"{module.name} ({module.path})"
