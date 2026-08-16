"""check_env_example.py — completeness gate for .env.example (REQ-SET-04).

Extracts every environment variable key read by agentic_app/config/env.py,
then asserts that all those keys appear as KEY=... lines in the repo-root
.env.example.  Exits non-zero and prints missing keys if the catalog is
incomplete.

Usage (from the agentic_app/ directory):
    python scripts/check_env_example.py

This script is re-used by the settingup.2 bring-up runbook as the acceptance
gate for REQ-SET-04.
"""
import re
import sys
from pathlib import Path

# Resolve repo root from this file's location: agentic_app/scripts/ -> repo root
SCRIPT_DIR = Path(__file__).resolve().parent
AGENTIC_APP_DIR = SCRIPT_DIR.parent
REPO_ROOT = AGENTIC_APP_DIR.parent

ENV_PY = AGENTIC_APP_DIR / "config" / "env.py"
# .env.example lives beside the backend (platform/backend/.env.example) post-restructure.
ENV_EXAMPLE = AGENTIC_APP_DIR / ".env.example"

# Keys read by env.py that are INTENTIONALLY excluded from .env.example.
# .env.example documents only what an operator must set; these are sourced elsewhere.

# Connector / OAuth / webhook secrets — populated in Azure Key Vault at connect-time
# (store_secret), resolved KV-first. Operators never put these in .env.
KV_MANAGED = {
    "ADO_PAT", "ADO_WEBHOOK_USER", "ADO_WEBHOOK_PASSWORD",
    "JIRA_API_TOKEN", "JIRA_EMAIL", "JIRA_URL", "JIRA_WEBHOOK_SECRET",
    "JIRA_OAUTH_CLIENT_ID", "JIRA_OAUTH_CLIENT_SECRET",
    "GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_PATH", "GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_WEBHOOK_SECRET",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET",
    "LITELLM_API_KEY",
}

# Optional / advanced knobs with code defaults — operator rarely overrides.
OPTIONAL_DEFAULTS = {
    "AGENTIC_INTERNAL_BASE_URL", "ANTHROPIC_MODEL_FAST", "ANTHROPIC_MODEL_STANDARD",
    "ANTHROPIC_MODEL_EXTENDED", "JWT_ALGORITHM", "ENABLE_OIDC", "OIDC_ISSUER_URL",
    "AUTH0_AUDIENCE", "OIDC_PROVIDER", "ENABLE_SCIM", "ENABLE_LITELLM", "LITELLM_BASE_URL",
    "LLM_TENANT_BUDGET_USD_DEFAULT", "LLM_TENANT_BUDGET_OVERRIDES", "WORKER_POOL_CONCURRENCY",
    "WORKER_RECLAIM_TIMEOUT_MS", "SLA_REQUIREMENTS_HOURS",
    "SLA_DESIGN_HOURS", "SLA_DEVELOPMENT_HOURS", "SLA_TESTING_HOURS", "SLA_GRACE_MINUTES",
    "SLA_CLARIFICATION_HOURS", "GOOGLE_API_KEY_design",
    # KV secret NAME overrides — have sensible defaults derived from AGENT_RUNTIME_MODE;
    # operators only set these when a vault uses non-standard secret naming.
    "KV_SECRET_POSTGRES_CONN", "KV_SECRET_POSTGRES_SYNC_CONN", "KV_SECRET_POSTGRES_MIGRATIONS_CONN",
}

# Legacy keys still referenced by env.py but slated for removal (AzureOpenAI).
# SQLSERVER_CONN_STRING is gone — it fed the Django-era SQL Server database, whose
# one-shot Postgres importer was removed along with the Django dependency.
LEGACY = {
    "AGENTIC_APP_PATH",
    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION", "OPENAI_API_KEY",
}

EXCLUDED = KV_MANAGED | OPTIONAL_DEFAULTS | LEGACY


def extract_env_py_keys(path: Path) -> set[str]:
    """Extract all os.environ[...] and os.environ.get(...) keys from env.py.

    Handles both:
      os.environ["KEY"]        — required (raises KeyError if absent)
      os.environ.get("KEY", ...)  — optional with default

    Uses mixed-case pattern to catch GOOGLE_API_KEY_design and similar
    non-all-uppercase keys that match the actual variable name exactly.
    """
    src = path.read_text(encoding="utf-8")
    # Match both os.environ["KEY"] and os.environ.get("KEY", default)
    pattern = r'os\.environ(?:\.get)?\(\s*["\']([A-Za-z][A-Za-z0-9_]+)["\']'
    return set(re.findall(pattern, src))


def extract_env_example_keys(path: Path) -> set[str]:
    """Extract KEY names from KEY=value lines in .env.example.

    Ignores blank lines and comment lines (starting with #).
    """
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key:
                keys.add(key)
    return keys


def main() -> int:
    if not ENV_PY.exists():
        print(f"ERROR: env.py not found at {ENV_PY}", file=sys.stderr)
        return 1

    if not ENV_EXAMPLE.exists():
        print(f"ERROR: .env.example not found at {ENV_EXAMPLE}", file=sys.stderr)
        print("  Create .env.example at the repo root documenting every variable.", file=sys.stderr)
        return 1

    env_py_keys = extract_env_py_keys(ENV_PY)
    env_example_keys = extract_env_example_keys(ENV_EXAMPLE)

    # Operator-required = everything env.py reads MINUS KV-managed/optional/legacy keys.
    required_keys = env_py_keys - EXCLUDED
    missing = sorted(required_keys - env_example_keys)

    if missing:
        print(f"FAIL: {len(missing)} operator-required key(s) missing from .env.example:")
        for key in missing:
            print(f"  - {key}")
        print(
            f"\nenv.py total: {len(env_py_keys)}  required: {len(required_keys)}  "
            f".env.example: {len(env_example_keys)}  (excluded: {len(EXCLUDED & env_py_keys)})"
        )
        return 1

    print(
        f"OK: all {len(required_keys)} operator-required keys are documented in .env.example "
        f"({len(EXCLUDED & env_py_keys)} keys excluded as KV-managed/optional/legacy; "
        f"{len(env_example_keys)} total .env.example keys)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
