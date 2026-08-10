---
name: security_static
description: Run a static-analysis security scanner over the source — Bandit (Python), dotnet build with Roslyn analyzers (.NET), or ESLint security plugin (JS) — and surface findings into TestingArtifact.security_findings alongside Trivy/Sonar pipeline output.
when_to_use: code_analysis is non-empty
runtime: shell
shell_command: bandit -r . -f json -o reports/security_static.json
shell_timeout_s: 300
output_parser: bandit_json
output_artifact_field: security_findings
inputs: []
outputs:
  finding_count: int
---

# Security Static Analysis — runtime: shell

Shell-runtime skill: does not prompt the LLM. Runs a security scanner whose JSON output is parsed via a registered parser and merged into `AggregatedResults.security_findings` (extending Trivy/Sonar findings from the ADO pipeline path).

The `shell_command` above defaults to **Bandit** (Python). For other languages, copy + edit:

- **Python:** `bandit -r . -f json -o reports/security_static.json` (parser: bandit_json)
- **.NET:** Roslyn analyzers via `dotnet build /p:RunAnalyzers=true /p:TreatWarningsAsErrors=false /flp:logfile=reports/security_static.log;verbosity=detailed` — needs a different parser (analyzer warnings, not Bandit JSON).
- **React / Node:** `npx eslint --ext .js,.jsx,.ts,.tsx --plugin security --format json -o reports/security_static.json src/`

Each scanner has its own findings JSON shape. The parser registered for this skill normalises them to the `SecurityFinding` pydantic schema (source / severity / rule_id / file / line / message / cwe).

# What gets flagged

Common Bandit findings (Python):
- `B102` — exec_used
- `B201` — flask_debug_true
- `B301` — pickle deserialisation
- `B501` — request_with_no_cert_validation
- `B608` — hardcoded_sql_expressions

Common ESLint security findings (Node):
- `detect-object-injection`
- `detect-eval-with-expression`
- `detect-non-literal-fs-filename`
- `detect-no-csrf-before-method-override`
