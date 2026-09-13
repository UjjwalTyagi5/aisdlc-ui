"""The assessment: every analysis step, assembled into `discovery_artifacts`.

`assess_repository` is the whole of Discovery's judgement and it is deterministic —
the same commit, the same `as_of` date and the same scanner findings always produce
the same document. That is what lets the BA sign it as the PLANNING BASELINE:
Design, Strategy and Testing all plan against these numbers, so they must not move
between two readings of one repository.

`assessment_markdown` renders the same data as the report a person reads — the
deliverable in the Orchestrator's panel and the exported .docx both come from it, so
the document and the stored data cannot disagree.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from agents_orchestrator.discovery_agent.analysis.eol import (
    deprecated_reason,
    runtime_status,
)
from agents_orchestrator.discovery_agent.analysis.graph import (
    build_dependency_graph,
    internal_references,
)
from agents_orchestrator.discovery_agent.analysis.inventory import (
    _TEST_MODULE_RE,
    scan_inventory,
)
from agents_orchestrator.discovery_agent.analysis.manifests import parse_module_manifest
from agents_orchestrator.discovery_agent.analysis.risk import TIERS, score_module

SCHEMA_VERSION = 1

#: Word-boundary keywords per ecosystem, used to tell whether the target stack is the
#: same language as a module (an upgrade codemods can do) or a different one (a
#: rewrite). Word boundaries matter: "JavaScript" must not read as "Java".
_ECOSYSTEM_WORDS: dict[str, re.Pattern[str]] = {
    ".NET": re.compile(r"(?i)(?:\.net\b|\bdotnet\b|\bc#|\bcsharp\b|\basp\.net\b|\bnet ?\d)"),
    "Java": re.compile(r"(?i)\b(?:java|spring|kotlin|jvm|quarkus|jakarta)\b"),
    "Node.js": re.compile(r"(?i)\b(?:node(?:\.js)?|javascript|typescript|nestjs|express)\b"),
    "Python": re.compile(r"(?i)\b(?:python|django|flask|fastapi)\b"),
    "Go": re.compile(r"(?i)\b(?:golang|go \d)"),
}

_GOLDEN_MASTER_NOTE = (
    "No behaviour baseline has been captured yet. Recording real inputs and outputs of "
    "the legacy system is what the Testing agent will diff the migrated code against; "
    "it is planned for that phase and this field is reserved for it."
)

_TIER_LABEL = {
    "mechanical": "Mechanical (codemod)",
    "llm_assisted": "LLM-assisted",
    "manual": "Manual-only",
}


def cross_language(ecosystem: str, target_stack: str) -> bool:
    """Is `target_stack` a different language from `ecosystem`?

    False when the target is empty or names nothing recognisable — "not known" is not
    evidence of a rewrite, and saying so would demote every module out of the
    mechanical tier on a brief that simply had not named a target yet.
    """
    target = (target_stack or "").strip()
    if not target:
        return False
    own = _ECOSYSTEM_WORDS.get(ecosystem)
    if own is not None and own.search(target):
        return False
    return any(p.search(target) for eco, p in _ECOSYSTEM_WORDS.items() if eco != ecosystem)


def _runtime_label(runtime) -> str:
    return f"{runtime.name} {runtime.version}" if runtime else "not declared"


def _attribute_vulnerabilities(modules, manifests, vulnerabilities):
    """module name → [finding], by the scanned file's directory first and by the
    package name second (a finding from a lock file two levels up still belongs to
    the module that declares the package)."""
    by_module: dict[str, list[dict]] = {m.name: [] for m in modules}
    paths = sorted(((m.path, m.name) for m in modules), key=lambda pm: -len(pm[0]))
    for finding in vulnerabilities or []:
        target = str(finding.get("target") or "").replace("\\", "/")
        owner = next(
            (name for path, name in paths
             if path != "." and (target == path or target.startswith(path + "/"))),
            None,
        )
        if owner is None:
            package = str(finding.get("package") or "").lower()
            owner = next(
                (m.name for m in modules
                 if any(d.name.lower() == package for d in manifests[m.name].dependencies)),
                None,
            )
        if owner is None and any(p == "." for p, _ in paths):
            owner = next(name for path, name in paths if path == ".")
        if owner is not None:
            by_module[owner].append(finding)
    return by_module


def assess_repository(
    root: Path,
    *,
    as_of: date | None = None,
    vulnerabilities: list[dict] | None = None,
    repository: dict | None = None,
    target_stack: str = "",
    scanners: dict | None = None,
) -> dict:
    """Assess the checked-out repository at `root`. See the module docstring."""
    root = Path(root)
    as_of = as_of or date.today()
    inventory = scan_inventory(root)
    modules = inventory.modules
    manifests = {m.name: parse_module_manifest(root, m) for m in modules}
    internal = internal_references(modules, manifests)
    internal_short = {m.name.split(":")[-1].lower() for m in modules}

    dependents: dict[str, list[str]] = {m.name: [] for m in modules}
    for source, targets in internal.items():
        for target in targets:
            dependents[target].append(source)

    # A module is tested if it has tests of its own, or a TEST module references it.
    tested = {m.name: m.has_tests for m in modules}
    for m in modules:
        if m.has_tests and _TEST_MODULE_RE.search(m.name):
            for target in internal[m.name]:
                tested[target] = True

    vulns_by_module = _attribute_vulnerabilities(modules, manifests, vulnerabilities)

    flags: dict[str, list[dict]] = {"eol": [], "deprecated": [], "vulnerable": []}
    module_rows: list[dict] = []
    for m in modules:
        facts = manifests[m.name]
        status = runtime_status(facts.runtime, as_of)
        if status.status in {"eol", "approaching"}:
            flags["eol"].append({
                "module": m.name, "runtime": _runtime_label(facts.runtime),
                "status": status.status,
                "eol_date": status.eol_date.isoformat() if status.eol_date else None,
                "note": status.note,
            })

        module_vulns = vulns_by_module.get(m.name, [])
        deps: list[dict] = []
        declared = set()
        deprecated_count = 0
        for dep in facts.dependencies:
            if dep.kind == "package" and dep.name.split(":")[-1].lower() in internal_short:
                continue  # an internal reference, shown as a project edge instead
            declared.add(dep.name.lower())
            reason = deprecated_reason(m.ecosystem, dep.name) if dep.kind == "package" else None
            dep_vulns = [
                v for v in module_vulns if str(v.get("package") or "").lower() == dep.name.lower()
            ]
            row = {
                "name": dep.name, "version": dep.version, "kind": dep.kind,
                "status": "vulnerable" if dep_vulns else ("deprecated" if reason else "ok"),
                "note": reason or "", "vulnerabilities": dep_vulns,
            }
            deps.append(row)
            if reason:
                deprecated_count += 1
                flags["deprecated"].append({
                    "module": m.name, "package": dep.name, "version": dep.version, "reason": reason,
                })
        # A finding for a package the module does not declare directly is transitive.
        for v in module_vulns:
            if str(v.get("package") or "").lower() not in declared:
                deps.append({
                    "name": v.get("package") or "", "version": v.get("installed_version") or "",
                    "kind": "package", "status": "vulnerable",
                    "note": "Transitive — found by the scanner, not declared by this module.",
                    "vulnerabilities": [v],
                })
                declared.add(str(v.get("package") or "").lower())
        for v in module_vulns:
            flags["vulnerable"].append({
                "module": m.name, "package": v.get("package"), "version": v.get("installed_version"),
                "cve": v.get("cve"), "severity": v.get("severity"),
                "fixed_version": v.get("fixed_version"), "title": v.get("title"),
            })

        high = sum(1 for v in module_vulns if str(v.get("severity")).lower() in {"critical", "high"})
        risk = score_module(
            m,
            runtime_status=status,
            dependency_count=sum(1 for d in deps if d["kind"] == "package"),
            deprecated=deprecated_count,
            vulnerable_high=high,
            vulnerable_other=len(module_vulns) - high,
            fan_in=len(dependents[m.name]),
            cross_language=cross_language(m.ecosystem, target_stack),
            tested=tested[m.name],
        )
        module_rows.append({
            "name": m.name, "path": m.path, "ecosystem": m.ecosystem, "manifest": m.manifest,
            "loc": m.loc, "files": m.files, "languages": m.languages,
            "vendored_files": m.vendored_files,
            "has_tests": tested[m.name],
            "runtime": {
                "name": facts.runtime.name if facts.runtime else "",
                "version": facts.runtime.version if facts.runtime else "",
                "status": status.status,
                "eol_date": status.eol_date.isoformat() if status.eol_date else None,
                "note": status.note,
            },
            "dependencies": deps,
            "depends_on": internal[m.name],
            "dependents": dependents[m.name],
            "blockers": list(m.blockers),
            "parse_error": facts.parse_error,
            "risk": risk.as_dict(),
        })

    scores = [row["risk"]["score"] for row in module_rows]
    summary = {
        "module_count": len(module_rows),
        "file_count": inventory.file_count,
        "loc": inventory.loc,
        "vendored_files": sum(m.vendored_files for m in modules),
        "languages": inventory.languages,
        "ecosystems": sorted({m.ecosystem for m in modules}),
        "tier_counts": {t: sum(1 for r in module_rows if r["risk"]["tier"] == t) for t in TIERS},
        "risk": {
            "average": round(sum(scores) / len(scores), 1) if scores else 0,
            "max": max(scores) if scores else 0,
        },
        "flag_counts": {k: len(v) for k, v in flags.items()},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "target_stack": target_stack or "",
        "repository": {
            "url": "", "branch": "", "commit": "", "provider": "", "name": root.name,
            **(repository or {}),
        },
        "summary": summary,
        "modules": sorted(module_rows, key=lambda r: (-r["risk"]["score"], r["name"])),
        "dependency_graph": build_dependency_graph(modules, manifests),
        "flags": flags,
        "scanners": scanners or {"trivy": "skipped", "note": "No vulnerability scan was run."},
        "golden_master": {"status": "not_captured", "ref": None, "note": _GOLDEN_MASTER_NOTE},
    }


# ── the document ─────────────────────────────────────────────────────────────


def _cell(value) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
#: CVEs named per package in the report; the full list is in the stored assessment.
_CVES_PER_PACKAGE = 3


def _vulnerability_lines(findings: list[dict]) -> list[str]:
    """One line per (module, package, version), worst first — not one per CVE.

    A real legacy system has hundreds of findings; one line each buries the few packages
    that actually matter. Each line says how many there are by severity and names the
    worst few. The stored assessment (and the page's Flags tab) keeps every finding.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for f in findings:
        key = (f.get("module") or "", f.get("package") or "?", f.get("version") or "")
        groups.setdefault(key, []).append(f)

    def rank(f: dict) -> int:
        sev = str(f.get("severity") or "UNKNOWN").upper()
        return _SEVERITY_ORDER.index(sev) if sev in _SEVERITY_ORDER else len(_SEVERITY_ORDER)

    def weight(items: list[dict]) -> tuple:
        counts = [sum(1 for f in items if rank(f) == i) for i in range(len(_SEVERITY_ORDER))]
        return tuple(-c for c in counts)

    lines = []
    for (module, package, version), items in sorted(groups.items(), key=lambda kv: (weight(kv[1]), kv[0])):
        items = sorted(items, key=rank)
        by_sev = [(sev.lower(), sum(1 for f in items if rank(f) == i))
                  for i, sev in enumerate(_SEVERITY_ORDER)]
        counts = ", ".join(f"{n} {sev}" for sev, n in by_sev if n)
        named = ", ".join(f"{f.get('cve')} ({str(f.get('severity') or '').upper()})"
                          for f in items[:_CVES_PER_PACKAGE] if f.get("cve"))
        more = f" and {len(items) - _CVES_PER_PACKAGE} more" if len(items) > _CVES_PER_PACKAGE else ""
        lines.append(
            f"- **{module}** — `{package}` {version}: {len(items)} known "
            f"vulnerabilit{'y' if len(items) == 1 else 'ies'} ({counts})"
            + (f" — {named}{more}" if named else "") + "."
        )
    return lines


def assessment_markdown(artifacts: dict, *, max_modules: int | None = None) -> str:
    """The assessment as a report. Headings are what make it a deliverable.

    `max_modules` bounds the per-module tables (modules are already ordered riskiest
    first) so the copy that goes into a chat turn stays inside a tool result's budget;
    the exported document passes None and lists every module.
    """
    repo = artifacts.get("repository") or {}
    summary = artifacts.get("summary") or {}
    all_modules = artifacts.get("modules") or []
    modules = all_modules if max_modules is None else all_modules[:max_modules]
    omitted = len(all_modules) - len(modules)
    flags = artifacts.get("flags") or {}
    tiers = summary.get("tier_counts") or {}
    name = repo.get("name") or repo.get("url") or "legacy repository"

    lines: list[str] = [f"# Discovery & Assessment — {name}", ""]
    meta = [f"Assessed {str(artifacts.get('as_of') or '')}".strip()]
    if repo.get("url"):
        meta.append(f"source `{repo['url']}`" + (f" @ `{repo['branch']}`" if repo.get("branch") else ""))
    if repo.get("commit"):
        meta.append(f"commit `{str(repo['commit'])[:10]}`")
    if artifacts.get("target_stack"):
        meta.append(f"target **{artifacts['target_stack']}**")
    lines += ["_" + " · ".join(meta) + "_", ""]

    lines += ["## Executive summary", ""]
    lines.append(
        f"- **{summary.get('module_count', 0)} modules**, {summary.get('loc', 0):,} lines of code "
        f"in {summary.get('file_count', 0):,} files ({', '.join(summary.get('ecosystems') or []) or 'no recognised build'})."
    )
    if summary.get("vendored_files"):
        lines.append(
            f"- {summary['vendored_files']:,} vendored front-end library file(s) (jQuery, Bootstrap, "
            "minified bundles…) are counted as files but not as lines of code."
        )
    lines.append(
        "- Migration tiers: "
        + ", ".join(f"{_TIER_LABEL[t]} **{tiers.get(t, 0)}**" for t in TIERS) + "."
    )
    fc = summary.get("flag_counts") or {}
    lines.append(
        f"- Flags: **{fc.get('eol', 0)}** end-of-life runtime(s), **{fc.get('deprecated', 0)}** "
        f"deprecated package(s), **{fc.get('vulnerable', 0)}** known vulnerabilit(ies)."
    )
    riskiest = [m for m in modules if m["risk"]["tier"] == "manual"][:5] or modules[:3]
    if riskiest:
        lines.append(
            "- Highest risk: " + ", ".join(f"**{m['name']}** ({m['risk']['score']})" for m in riskiest) + "."
        )
    lines.append("")

    if omitted:
        lines += [f"_The tables below show the {len(modules)} riskiest modules; {omitted} more are "
                  "on the Discovery page and in the exported report._", ""]

    lines += ["## Inventory", "", "| Module | Path | Ecosystem | Runtime | LOC | Tests |",
              "|---|---|---|---|---:|---|"]
    for m in modules:
        rt = m.get("runtime") or {}
        runtime = f"{rt.get('name', '')} {rt.get('version', '')}".strip() or "—"
        lines.append(
            f"| {_cell(m['name'])} | `{_cell(m['path'])}` | {_cell(m['ecosystem'])} | "
            f"{_cell(runtime)} ({_cell(rt.get('status'))}) | {m['loc']:,} | {'yes' if m['has_tests'] else 'no'} |"
        )
    lines.append("")

    lines += ["## Dependency graph", ""]
    for m in modules:
        externals = sum(1 for d in m["dependencies"] if d["kind"] == "package")
        internal = ", ".join(m["depends_on"]) or "no other module"
        lines.append(f"- **{m['name']}** → {internal}; {externals} external package(s).")
    lines.append("")

    lines += ["## End-of-life, deprecated and vulnerable dependencies", ""]
    lines += ["### End-of-life runtimes", ""]
    if flags.get("eol"):
        for f in flags["eol"]:
            when = f" (support ended {f['eol_date']})" if f.get("eol_date") and f["status"] == "eol" else (
                f" (support ends {f['eol_date']})" if f.get("eol_date") else "")
            lines.append(f"- **{f['module']}** — {f['runtime']}: {f['status']}{when}.")
    else:
        lines.append("- None found.")
    lines += ["", "### Deprecated packages", ""]
    if flags.get("deprecated"):
        for f in flags["deprecated"]:
            lines.append(f"- **{f['module']}** — `{f['package']}` {f.get('version') or ''}: {f['reason']}")
    else:
        lines.append("- None found.")
    lines += ["", "### Known vulnerabilities", ""]
    scanner = artifacts.get("scanners") or {}
    if flags.get("vulnerable"):
        lines += _vulnerability_lines(flags["vulnerable"])
    else:
        lines.append(f"- None reported. Scanner: {scanner.get('trivy', 'skipped')}"
                     + (f" — {scanner['note']}" if scanner.get("note") else "") + ".")
    lines.append("")

    lines += ["## Module risk and migration tier", "",
              "| Module | Score | Tier | Main factors |", "|---|---:|---|---|"]
    for m in modules:
        top = sorted(m["risk"]["factors"], key=lambda f: -f["points"])[:3]
        reasons = "; ".join(f"{f['factor']} +{f['points']}" for f in top) or "—"
        lines.append(
            f"| {_cell(m['name'])} | {m['risk']['score']} | {_TIER_LABEL.get(m['risk']['tier'], m['risk']['tier'])} | {_cell(reasons)} |"
        )
    lines.append("")

    golden = artifacts.get("golden_master") or {}
    lines += ["## Behaviour baseline", "", f"- Status: {golden.get('status', 'not_captured')}. {golden.get('note', '')}", ""]

    lines += [
        "## Next steps", "",
        "- The BA accepts this assessment as the planning baseline by approving the exported report.",
        "- Design decides the target architecture and migration pattern from it; Strategy sequences the "
        "modules into waves, lowest risk first.",
        "",
    ]
    return "\n".join(lines)
