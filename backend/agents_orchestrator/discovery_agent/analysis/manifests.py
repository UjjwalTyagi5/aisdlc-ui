"""Runtime and dependencies, read from each module's own manifest.

One parser per ecosystem the platform meets in legacy estates: .NET (both the
pre-SDK `.csproj` + `packages.config` shape and the SDK style), Maven, Gradle,
npm and Python. Each is a best-effort READ of a file a person wrote, so none of
them raises: a manifest that cannot be parsed yields no runtime, no dependencies
and a `parse_error` the assessment reports, rather than an exception that loses the
whole repository's assessment over one broken file.
"""
from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agents_orchestrator.discovery_agent.analysis.eol import Runtime
from agents_orchestrator.discovery_agent.analysis.inventory import ModuleFacts


@dataclass(frozen=True)
class Dependency:
    name: str
    version: str
    kind: str = "package"  # "package" | "framework-assembly"


@dataclass
class ManifestFacts:
    runtime: Runtime | None = None
    dependencies: list[Dependency] = field(default_factory=list)
    #: Posix paths (relative to the repository root) of modules this one references.
    project_references: list[str] = field(default_factory=list)
    parse_error: str = ""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_ns(tree: ET.Element) -> ET.Element:
    """Drop XML namespaces so `find("PropertyGroup")` works on old and new csproj
    and on any pom, instead of every lookup carrying a `{uri}` prefix."""
    for el in tree.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return tree


# ── .NET ─────────────────────────────────────────────────────────────────────

_TFM_FRAMEWORK = re.compile(r"^net(\d)(\d)(\d)?$")          # net452, net48, net472
_TFM_CORE = re.compile(r"^netcoreapp(\d+\.\d+)$")
_TFM_MODERN = re.compile(r"^net(\d+\.\d+)(?:-[a-z0-9.]+)?$")  # net6.0, net8.0-windows
_TFM_STANDARD = re.compile(r"^netstandard(\d+\.\d+)$")


def runtime_from_tfm(tfm: str) -> Runtime | None:
    tfm = (tfm or "").strip().lower()
    if m := _TFM_FRAMEWORK.match(tfm):
        return Runtime(".NET Framework", ".".join(g for g in m.groups() if g))
    if m := _TFM_CORE.match(tfm):
        return Runtime(".NET Core", m.group(1))
    if m := _TFM_MODERN.match(tfm):
        return Runtime(".NET", m.group(1))
    if m := _TFM_STANDARD.match(tfm):
        return Runtime(".NET Standard", m.group(1))
    return None


def _module_relative(module_dir: str, include: str) -> str:
    """Resolve a `<ProjectReference Include="..\\Other\\Other.csproj">` against the
    referencing project's directory, to the referenced project's directory."""
    target = PurePosixPath(module_dir if module_dir != "." else "") / include.replace("\\", "/")
    parts: list[str] = []
    for part in target.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    return str(PurePosixPath(*parts).parent) if parts else "."


def _parse_dotnet(root: Path, module: ModuleFacts) -> ManifestFacts:
    facts = ManifestFacts()
    project_file = root / module.manifest
    tree = _strip_ns(ET.fromstring(_read(project_file)))

    tfv = tree.findtext(".//TargetFrameworkVersion")
    if tfv:
        facts.runtime = Runtime(".NET Framework", tfv.strip().lstrip("vV"))
    else:
        tfms = tree.findtext(".//TargetFramework") or tree.findtext(".//TargetFrameworks") or ""
        first = next((t for t in tfms.split(";") if t.strip()), "")
        facts.runtime = runtime_from_tfm(first)

    for ref in tree.iter("PackageReference"):
        name = ref.get("Include") or ref.get("Update")
        if not name:
            continue
        version = ref.get("Version") or (ref.findtext("Version") or "")
        facts.dependencies.append(Dependency(name.strip(), version.strip()))

    for ref in tree.iter("FrameworkReference"):
        if ref.get("Include"):
            facts.dependencies.append(Dependency(ref.get("Include").strip(), "", "framework-assembly"))

    for ref in tree.iter("Reference"):
        include = (ref.get("Include") or "").split(",", 1)[0].strip()
        # A <Reference> WITH a HintPath is a package's DLL, already listed from
        # packages.config below; one without is a framework assembly.
        if include and ref.find("HintPath") is None:
            if include.startswith(("System", "Microsoft.")) and include != "System":
                facts.dependencies.append(Dependency(include, "", "framework-assembly"))

    for ref in tree.iter("ProjectReference"):
        include = ref.get("Include")
        if include:
            facts.project_references.append(_module_relative(module.path, include))

    packages_config = project_file.parent / "packages.config"
    if packages_config.exists():
        cfg = _strip_ns(ET.fromstring(_read(packages_config)))
        for pkg in cfg.iter("package"):
            if pkg.get("id"):
                facts.dependencies.append(Dependency(pkg.get("id"), pkg.get("version") or ""))
    return facts


# ── Java ─────────────────────────────────────────────────────────────────────


def _java_version(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("1."):
        return value[2:]
    return value.split(".")[0] if value else ""


def _parse_pom(root: Path, module: ModuleFacts) -> ManifestFacts:
    facts = ManifestFacts()
    tree = _strip_ns(ET.fromstring(_read(root / module.manifest)))
    props = {
        child.tag: (child.text or "").strip()
        for child in (tree.find("properties") if tree.find("properties") is not None else [])
    }

    def resolve(value: str) -> str:
        m = re.fullmatch(r"\$\{([^}]+)\}", value or "")
        return props.get(m.group(1), value) if m else (value or "")

    version = (
        props.get("maven.compiler.release") or props.get("maven.compiler.source")
        or props.get("java.version") or ""
    )
    if not version:
        for plugin in tree.iter("plugin"):
            if plugin.findtext("artifactId") == "maven-compiler-plugin":
                version = plugin.findtext(".//release") or plugin.findtext(".//source") or ""
    if version:
        facts.runtime = Runtime("Java", _java_version(resolve(version)))

    deps = tree.find("dependencies")
    for dep in (deps if deps is not None else []):
        group, artifact = dep.findtext("groupId") or "", dep.findtext("artifactId") or ""
        if artifact:
            facts.dependencies.append(
                Dependency(f"{group}:{artifact}" if group else artifact, resolve(dep.findtext("version") or ""))
            )
    return facts


_GRADLE_DEP = re.compile(
    r"(?:implementation|api|compile|compileOnly|runtimeOnly|testImplementation|testCompile)"
    r"\s*\(?\s*['\"]([^:'\"]+):([^:'\"]+)(?::([^'\"]+))?['\"]"
)
_GRADLE_JAVA = re.compile(
    r"(?:sourceCompatibility\s*=\s*['\"]?(?:JavaVersion\.VERSION_)?([0-9._]+)"
    r"|JavaLanguageVersion\.of\((\d+)\))"
)


def _parse_gradle(root: Path, module: ModuleFacts) -> ManifestFacts:
    facts = ManifestFacts()
    text = _read(root / module.manifest)
    if m := _GRADLE_JAVA.search(text):
        facts.runtime = Runtime("Java", _java_version((m.group(1) or m.group(2) or "").replace("_", ".")))
    for m in _GRADLE_DEP.finditer(text):
        facts.dependencies.append(Dependency(f"{m.group(1)}:{m.group(2)}", m.group(3) or ""))
    return facts


# ── Node.js ──────────────────────────────────────────────────────────────────


def _parse_package_json(root: Path, module: ModuleFacts) -> ManifestFacts:
    facts = ManifestFacts()
    data = json.loads(_read(root / module.manifest))
    engine = str((data.get("engines") or {}).get("node") or "")
    if not engine:
        nvmrc = (root / module.manifest).parent / ".nvmrc"
        engine = _read(nvmrc).strip() if nvmrc.exists() else ""
    if m := re.search(r"(\d+)", engine):
        facts.runtime = Runtime("Node.js", m.group(1))
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            facts.dependencies.append(Dependency(name, str(version)))
    return facts


# ── Python ───────────────────────────────────────────────────────────────────

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:([=<>!~]=?)\s*([^\s;#,]+))?")


def _python_runtime(value: str) -> Runtime | None:
    m = re.search(r"(\d+\.\d+)", value or "")
    return Runtime("Python", m.group(1)) if m else None


def _parse_python(root: Path, module: ModuleFacts) -> ManifestFacts:
    facts = ManifestFacts()
    directory = (root / module.manifest).parent
    pyproject = directory / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(_read(pyproject))
        project = data.get("project") or {}
        facts.runtime = _python_runtime(project.get("requires-python", ""))
        for req in project.get("dependencies") or []:
            if m := _REQ_LINE.match(req):
                facts.dependencies.append(Dependency(m.group(1), m.group(3) or ""))
        poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
        for name, spec in poetry.items():
            if name.lower() == "python":
                facts.runtime = facts.runtime or _python_runtime(str(spec))
                continue
            facts.dependencies.append(Dependency(name, str(spec) if isinstance(spec, str) else ""))
    requirements = directory / "requirements.txt"
    if requirements.exists():
        for line in _read(requirements).splitlines():
            if not line.strip() or line.lstrip().startswith(("#", "-", "git+", "http")):
                continue
            if m := _REQ_LINE.match(line):
                facts.dependencies.append(Dependency(m.group(1), m.group(3) or ""))
    for pin in ("runtime.txt", ".python-version"):
        path = directory / pin
        if facts.runtime is None and path.exists():
            facts.runtime = _python_runtime(_read(path))
    return facts


# ── dispatch ─────────────────────────────────────────────────────────────────


def parse_module_manifest(root: Path, module: ModuleFacts) -> ManifestFacts:
    """The runtime and dependencies `module` declares. Never raises."""
    if not module.manifest:
        return ManifestFacts()
    name = Path(module.manifest).name
    try:
        if module.ecosystem == ".NET":
            return _parse_dotnet(root, module)
        if name == "pom.xml":
            return _parse_pom(root, module)
        if name.startswith("build.gradle"):
            return _parse_gradle(root, module)
        if name == "package.json":
            return _parse_package_json(root, module)
        if module.ecosystem == "Python":
            return _parse_python(root, module)
        if name == "go.mod":
            text = _read(root / module.manifest)
            m = re.search(r"(?m)^go\s+(\d+\.\d+)", text)
            return ManifestFacts(runtime=Runtime("Go", m.group(1)) if m else None)
    except Exception as exc:  # noqa: BLE001 — one broken file must not cost the assessment
        return ManifestFacts(parse_error=f"{module.manifest}: {type(exc).__name__}: {exc}"[:300])
    return ManifestFacts()
