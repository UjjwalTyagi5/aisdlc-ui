---
name: dependency_scan
description: Audit installed packages for known CVEs — pip-audit (Python), npm audit (Node), or dotnet list package --vulnerable (.NET). Surfaces results into TestingArtifact.dependency_vulns.
when_to_use: code_analysis is non-empty (a project to scan exists)
runtime: shell
shell_command: pip-audit -f json -o reports/dependency_scan.json
shell_timeout_s: 300
output_parser: pip_audit_json
output_artifact_field: dependency_vulns
inputs: []
outputs:
  vuln_count: int
---

# Dependency Vulnerability Scan — runtime: shell

Shell-runtime skill: scans installed dependencies against CVE databases (PyPI Advisory DB / npm advisories / NuGet). Each finding is a `DependencyVulnerability` pydantic model with package name + installed version + severity + CVE ID + fix versions.

Per-language commands (copy + edit frontmatter for the language you target):

- **Python:** `pip-audit -f json -o reports/dependency_scan.json` (parser: pip_audit_json)
- **Node.js / React:** `npm audit --json > reports/dependency_scan.json` (parser: npm_audit_json)
- **.NET:** `dotnet list package --vulnerable --include-transitive --format json > reports/dependency_scan.json` — needs a custom parser (different schema from npm/pip-audit). Skip for now or add a `dotnet_vuln_json` parser to skill_parsers.py first.

# Why this matters

Many CVEs in production come from outdated transitive dependencies. A clean code review + 100% test coverage doesn't help if `lodash@4.17.10` has a known prototype-pollution CVE.

This skill catches:
- Direct dependencies with published CVEs
- Transitive dependencies with CVEs
- Packages where a fix-version is available (so deployment knows it can bump and verify)
