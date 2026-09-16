"""The security review of a checked-out branch: secrets, code, dependencies, SBOM.

WHY THIS IS NOT LEFT TO THE MODEL. A security section written from a model's reading of
the code says what it noticed; this says what three scanners found, and — just as
important — which of them ran. Every scanner's status is recorded ("ok", "error" with
its message, "unavailable"), so a report can never present a scan that did not happen
as a clean result.

    Gitleaks   hardcoded secrets and credentials (values redacted)
    Semgrep    static analysis against the OWASP Top 10 rules
    Trivy      known vulnerabilities in the dependencies
    SBOM       every declared dependency, its version and whether Trivy flagged it

DEPENDENCY VERSIONS ARE RESOLVED, NOT GUESSED. An npm project with no lockfile declares
ranges ("^4.19.2"); Trivy only reads lockfiles, so on such a project it reports zero
vulnerabilities because it looked at nothing. For each such package.json the declared
ranges are resolved into a lockfile in a SCRATCH copy outside the checkout (`npm install
--package-lock-only --ignore-scripts` — nothing is installed and no package script
runs), Trivy scans that, and the SBOM records the versions as "resolved at scan time".
When resolution fails the SBOM says so and keeps the ranges; it never pretends.

The checkout itself is never modified.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Callable, Optional

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "bin", "obj", "dist", "build"}
_NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")
_MAX_MANIFEST_BYTES = 400_000


def _timed(fn: Callable[[], dict]) -> tuple[dict, float]:
    started = time.monotonic()
    result = fn()
    return result, round(time.monotonic() - started, 1)


def _run_wrapper(tool, target: str) -> dict:
    """Call one of the security agent's scanner tools and parse its JSON."""
    raw = tool.func(target_path=target) if hasattr(tool, "func") else tool(target_path=target)
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"status": "error", "message": f"unreadable scanner output: {str(raw)[:200]}", "findings": []}


def _relative(path: str, root: pathlib.Path) -> str:
    p = (path or "").replace("\\", "/")
    root_s = str(root).replace("\\", "/").rstrip("/") + "/"
    if p.startswith(root_s):
        return p[len(root_s):]
    return p.lstrip("./")


def _manifests(root: pathlib.Path) -> list[pathlib.Path]:
    from agents_orchestrator.security_agent.tools.security_tools import _is_manifest  # noqa: PLC0415

    found = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if _is_manifest(fn):
                found.append(pathlib.Path(dirpath) / fn)
    return sorted(found)


def _npm_components(manifest: pathlib.Path, rel: str) -> list[dict]:
    data = json.loads(manifest.read_text(encoding="utf-8", errors="replace")[:_MAX_MANIFEST_BYTES])
    out = []
    for section, scope in (("dependencies", "runtime"), ("devDependencies", "development"),
                           ("optionalDependencies", "optional"), ("peerDependencies", "peer")):
        for name, spec in (data.get(section) or {}).items():
            out.append({"name": name, "declared": str(spec), "version": "", "scope": scope,
                        "manifest": rel, "ecosystem": "npm"})
    return out


def _resolve_npm(manifest: pathlib.Path, scratch: pathlib.Path) -> tuple[Optional[pathlib.Path], str]:
    """Resolve a lockfile-less package.json into `scratch`. (dir, "") or (None, reason)."""
    npm = shutil.which("npm")
    if not npm:
        return None, "npm is not installed, so the declared version ranges could not be resolved"
    scratch.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, scratch / "package.json")
    proc = subprocess.run(
        [npm, "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=str(scratch), capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not (scratch / "package-lock.json").is_file():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, f"npm could not resolve the dependency versions: {(detail[-1] if detail else f'exit {proc.returncode}')[:200]}"
    return scratch, ""


def _lock_packages(lock_path: pathlib.Path) -> tuple[dict[str, str], list[dict]]:
    """From an npm lockfile (v2/v3 `packages` map): the direct dependencies' resolved
    versions, and EVERY installed package with the direct dependency that brings it in.

    `via` is what makes a transitive vulnerability actionable: "tar 6.2.1" is not in
    package.json, "tar 6.2.1 via sqlite3" says which upgrade removes it.
    """
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: dict = lock.get("packages") or {}
    root = packages.get("") or {}

    def _name(key: str) -> str:
        return key.rsplit("node_modules/", 1)[-1]

    def _resolve(parent_key: str, dep: str) -> Optional[str]:
        # npm's lookup: nested under the parent, then each ancestor, then top level.
        base = parent_key
        while True:
            candidate = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
            if candidate in packages:
                return candidate
            if not base:
                return None
            cut = base.rfind("/node_modules/")
            base = base[:cut] if cut != -1 else ""

    direct: dict[str, str] = {}
    via: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for dep in (root.get(section) or {}):
            key = _resolve("", dep)
            if key is None:
                continue
            direct[dep] = str((packages.get(key) or {}).get("version") or "")
            queue = [key]
            while queue:
                current = queue.pop(0)
                if current in via:
                    continue
                via[current] = dep
                meta = packages.get(current) or {}
                for child_section in ("dependencies", "optionalDependencies", "peerDependencies"):
                    for child in (meta.get(child_section) or {}):
                        child_key = _resolve(current, child)
                        if child_key and child_key not in via:
                            queue.append(child_key)

    listed: list[dict] = []
    for key, meta in packages.items():
        if not key or not isinstance(meta, dict) or "node_modules/" not in key:
            continue
        name = _name(key)
        listed.append({
            "name": name,
            "version": str(meta.get("version") or ""),
            "license": str(meta.get("license") or ""),
            "scope": "development" if meta.get("dev") else ("optional" if meta.get("optional") else "runtime"),
            "direct": key == f"node_modules/{name}" and name in direct,
            "via": via.get(key, ""),
        })
    return direct, listed


def run_code_security_scan(work_dir: str, *, progress: Callable[[str], None] = lambda _m: None) -> dict:
    """Scan a checkout. Never raises for a scanner problem — each one's status says it."""
    from agents_orchestrator.security_agent.tools.gitleaks_tool import run_gitleaks_scan  # noqa: PLC0415
    from agents_orchestrator.security_agent.tools.security_tools import _parse_manifest  # noqa: PLC0415
    from agents_orchestrator.security_agent.tools.semgrep_sast_tool import run_semgrep_sast  # noqa: PLC0415
    from agents_orchestrator.security_agent.tools.trivy_tool import run_trivy_scan  # noqa: PLC0415

    root = pathlib.Path(work_dir)
    if not root.is_dir():
        raise RuntimeError(f"The review checkout does not exist: {work_dir}")

    scanners: list[dict] = []

    def _record(name: str, purpose: str, result: dict, seconds: float, extra: str = "") -> None:
        status = result.get("status") or "error"
        message = str(result.get("message") or "")
        if extra:
            message = f"{message} {extra}".strip()
        scanners.append({
            "name": name, "purpose": purpose, "status": status, "seconds": seconds,
            "findings": len(result.get("findings") or []) if status == "ok" else None,
            "message": message,
        })

    # ── secrets ──
    progress("Scanning for hardcoded secrets (Gitleaks)…")
    gl, secs = _timed(lambda: _run_wrapper(run_gitleaks_scan, str(root)))
    _record("Gitleaks", "Hardcoded secrets and credentials", gl, secs)
    secrets = [
        {"rule": f.get("rule_id", ""), "description": f.get("description", ""),
         "file": _relative(f.get("file", ""), root), "line": f.get("line", 0)}
        for f in (gl.get("findings") or [])
    ] if gl.get("status") == "ok" else []

    # ── code ──
    progress("Running static analysis against the OWASP Top 10 rules (Semgrep)…")
    sg, secs = _timed(lambda: _run_wrapper(run_semgrep_sast, str(root)))
    _record("Semgrep", "Static analysis (OWASP Top 10 rules)", sg, secs)
    sast = [
        {"rule": f.get("rule_id", ""), "severity": (f.get("severity") or "warning").lower(),
         "message": f.get("message", ""), "file": _relative(f.get("file", ""), root),
         "line": f.get("line_start", 0),
         "owasp": f.get("owasp_category") or [], "cwe": f.get("cwe") or []}
        for f in (sg.get("findings") or [])
    ] if sg.get("status") == "ok" else []

    # ── dependencies + SBOM ──
    progress("Building the SBOM and checking dependencies for known vulnerabilities (Trivy)…")
    components: list[dict] = []
    manifests: list[str] = []
    resolution_notes: list[str] = []
    vulnerabilities: list[dict] = []
    trivy_targets: list[tuple[str, pathlib.Path]] = [("checkout", root)]
    scratch_root = pathlib.Path(tempfile.mkdtemp(prefix="code-review-sbom-"))
    try:
        for manifest in _manifests(root):
            rel = _relative(str(manifest), root)
            manifests.append(rel)
            if manifest.name == "package.json":
                try:
                    comps = _npm_components(manifest, rel)
                except (OSError, json.JSONDecodeError) as exc:
                    resolution_notes.append(f"{rel}: unreadable ({type(exc).__name__})")
                    continue
                committed_lock = manifest.parent / "package-lock.json"
                has_lock = any((manifest.parent / lock).is_file() for lock in _NPM_LOCKS)
                lock_path: Optional[pathlib.Path] = None
                source = ""
                if committed_lock.is_file():
                    lock_path, source = committed_lock, "lockfile"
                elif has_lock:
                    # yarn / pnpm: Trivy reads them from the checkout; their formats are
                    # not parsed here, so the SBOM keeps the declared ranges and says so.
                    for c in comps:
                        c["version_source"] = "declared range (yarn/pnpm lockfile not parsed)"
                    resolution_notes.append(f"{rel}: versions come from a yarn/pnpm lockfile Trivy reads directly")
                elif comps:
                    resolved_dir, reason = _resolve_npm(manifest, scratch_root / str(len(trivy_targets)))
                    if resolved_dir is None:
                        resolution_notes.append(f"{rel}: {reason}")
                        for c in comps:
                            c["version_source"] = "declared range only"
                    else:
                        lock_path, source = resolved_dir / "package-lock.json", "resolved at scan time"
                        trivy_targets.append((rel, resolved_dir))
                        resolution_notes.append(
                            f"{rel}: no lockfile is committed — versions were resolved from the declared "
                            "ranges at scan time, so they are what a fresh install would get today"
                        )
                if lock_path is not None:
                    try:
                        direct_versions, installed = _lock_packages(lock_path)
                    except (OSError, json.JSONDecodeError) as exc:
                        resolution_notes.append(f"{rel}: lockfile unreadable ({type(exc).__name__})")
                        installed, direct_versions = [], {}
                    declared = {c["name"]: c for c in comps}
                    for c in comps:
                        c["version"] = direct_versions.get(c["name"], "")
                        c["version_source"] = source if c["version"] else "not resolved"
                        c["direct"] = True
                        c["via"] = ""
                    for pkg in installed:
                        if pkg["direct"] and pkg["name"] in declared:
                            declared[pkg["name"]]["license"] = pkg["license"]
                            continue
                        comps.append({
                            "name": pkg["name"], "declared": "", "version": pkg["version"],
                            "license": pkg["license"], "scope": pkg["scope"], "manifest": rel,
                            "ecosystem": "npm", "version_source": source, "direct": False, "via": pkg["via"],
                        })
                for c in comps:
                    c.setdefault("direct", True)
                    c.setdefault("via", "")
                    c.setdefault("license", "")
                components.extend(comps)
            else:
                try:
                    text = manifest.read_text(encoding="utf-8", errors="replace")[:_MAX_MANIFEST_BYTES]
                except OSError:
                    continue
                for c in _parse_manifest(manifest.name, text, rel):
                    components.append({
                        "name": c.get("name", ""), "declared": c.get("version", ""),
                        "version": c.get("version", ""), "scope": "runtime", "manifest": rel,
                        "ecosystem": manifest.suffix.lstrip(".") or manifest.name,
                        "version_source": "manifest", "direct": True, "via": "", "license": "",
                    })

        trivy_statuses: list[tuple[str, dict, float]] = []
        for label, target in trivy_targets:
            tv, secs = _timed(lambda t=target: _run_wrapper(run_trivy_scan, str(t)))
            trivy_statuses.append((label, tv, secs))
            if tv.get("status") == "ok":
                for f in tv.get("findings") or []:
                    vulnerabilities.append({
                        "id": f.get("cve", ""), "severity": (f.get("severity") or "unknown").lower(),
                        "package": f.get("package", ""), "installed": f.get("installed_version", ""),
                        "fixed": f.get("fixed_version", ""), "title": f.get("title", ""),
                        "manifest": label if label != "checkout" else _relative(f.get("target", ""), root),
                    })
        failed = [(label, tv) for label, tv, _ in trivy_statuses if tv.get("status") != "ok"]
        combined = {
            "status": "ok" if not failed else (failed[0][1].get("status") or "error"),
            "message": "; ".join(f"{label}: {tv.get('message', '')}" for label, tv in failed),
            "findings": vulnerabilities,
        }
        _record("Trivy", "Known vulnerabilities in dependencies", combined,
                round(sum(secs for _, _, secs in trivy_statuses), 1))
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    # De-duplicate vulnerabilities reported by both targets.
    seen: set[tuple] = set()
    unique_vulns = []
    for v in vulnerabilities:
        key = (v["id"], v["package"], v["installed"])
        if key not in seen:
            seen.add(key)
            unique_vulns.append(v)
    trivy_ok = any(s["name"] == "Trivy" and s["status"] == "ok" for s in scanners)
    for c in components:
        if not trivy_ok:
            c["vulnerabilities"] = None
            continue
        c["vulnerabilities"] = sum(
            1 for v in unique_vulns
            if v["package"].lower() == c["name"].lower() and (not c.get("version") or v["installed"] == c["version"])
        )

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "scanners": scanners,
        "secrets": secrets,
        "sast": sast,
        "vulnerabilities": unique_vulns,
        "sbom": {
            "components": components,
            "manifests": manifests,
            "notes": resolution_notes,
        },
        "totals": {
            "secrets": len(secrets),
            "sast": len(sast),
            "vulnerabilities": len(unique_vulns),
            "vulnerabilities_high": sum(1 for v in unique_vulns if v["severity"] in ("critical", "high")),
            "components": len(components),
            "vulnerable_components": sum(1 for c in components if c.get("vulnerabilities")),
            "scanners_failed": sum(1 for s in scanners if s["status"] != "ok"),
        },
    }
