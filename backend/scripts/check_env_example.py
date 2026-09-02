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

# KV_MANAGED is now EMPTY, and that is the point. It used to list the connector OAuth
# client ids, the GitHub App id and key paths, and the inbound webhook secrets — all
# "populated in Azure Key Vault at connect-time, operators never put these in .env".
# None of them are read by env.py any more: the OAuth flow is gone and the webhook
# secrets are per-tenant. An operator configures no connector value of any kind, so
# there is nothing left for this set to excuse. It stays as a named empty set rather
# than being deleted so that adding an entry is a visible, deliberate act.
KV_MANAGED: set[str] = set()

# Everything hydrated from Key Vault when ENV != dev is Key-Vault-managed by definition.
# Derived from the single source of truth rather than restated here, so adding a secret
# to PLATFORM_SECRETS can never silently make this gate demand it in .env.example.
try:
    sys.path.insert(0, str(AGENTIC_APP_DIR))
    from config.secret_bootstrap import PLATFORM_SECRETS as _PLATFORM_SECRETS
    KV_MANAGED |= set(_PLATFORM_SECRETS)
except Exception:  # noqa: BLE001 — the gate must still run without the app importable
    pass

# Not configuration: read from the ambient process environment, never set by an operator.
AMBIENT = {"PATH"}

# Optional / advanced knobs with code defaults — operator rarely overrides.
OPTIONAL_DEFAULTS = {
    "AGENTIC_INTERNAL_BASE_URL",
    "JWT_ALGORITHM", "ENABLE_OIDC", "OIDC_ISSUER_URL",
    "AUTH0_AUDIENCE", "OIDC_PROVIDER", "ENABLE_SCIM", "ENABLE_LITELLM", "LITELLM_BASE_URL",
    "LLM_TENANT_BUDGET_USD_DEFAULT", "LLM_TENANT_BUDGET_OVERRIDES",
    "WORKER_RECLAIM_TIMEOUT_MS",
    # KV secret NAME overrides — have sensible defaults derived from AGENT_RUNTIME_MODE;
    # operators only set these when a vault uses non-standard secret naming.
    "KV_SECRET_POSTGRES_CONN", "KV_SECRET_POSTGRES_SYNC_CONN", "KV_SECRET_POSTGRES_MIGRATIONS_CONN",
}

# SQLSERVER_CONN_STRING is gone — it fed the Django-era SQL Server database, whose
# one-shot Postgres importer was removed along with the Django dependency.
# The AzureOpenAI keys that used to sit here were "slated for removal" and have now
# been removed from env.py outright (zero consumers), along with OPENAI_API_KEY —
# which LiteLLM would otherwise read as an implicit platform-key fallback.
LEGACY = {
    "AGENTIC_APP_PATH",
}

EXCLUDED = KV_MANAGED | OPTIONAL_DEFAULTS | LEGACY | AMBIENT


def extract_env_py_keys(path: Path) -> set[str]:
    """Extract every environment key env.py reads.

    Handles all three spellings:
      os.environ["KEY"]           — required (raises KeyError if absent)
      os.environ.get("KEY", ...)  — optional with default
      os.getenv("KEY", ...)       — the same thing under a different name

    os.getenv was missing from this pattern, which left the gate blind to the whole
    single-organization bootstrap block — DEFAULT_ORG_SLUG, DEFAULT_ORG_NAME,
    ORG_ADMIN_EMAILS, ORG_ADMIN_PASSWORD. Four operator-facing settings, one of them
    the initial admin password, that this completeness check silently never covered.

    Mixed case is still accepted so a non-all-uppercase key cannot slip past.
    """
    src = path.read_text(encoding="utf-8")
    pattern = r'os\.(?:environ(?:\.get)?|getenv)\(\s*["\']([A-Za-z][A-Za-z0-9_]+)["\']'
    return set(re.findall(pattern, src))


def extract_env_example_keys(path: Path) -> set[str]:
    """Extract KEY names from KEY=value lines in .env.example.

    A COMMENTED example (`# SMTP_USE_TLS=true`) counts as documented. That is the
    file's own convention for "optional — here is the value if you want it", used for
    most of the SMTP, MCP and token-TTL settings. Treating those as missing made this
    gate report 21 false positives against a file that documents every one of them,
    which is why it was not wired into CI.

    Prose comments are excluded by requiring the commented line to look like an
    assignment to a plausible env-var name.
    """
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
            if not re.match(r'^[A-Za-z][A-Za-z0-9_]*\s*=', stripped):
                continue  # prose, not a commented setting
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
