"""Central environment loader for agentic_app.

Import constants from here — do not call os.getenv elsewhere.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]  # platform/backend
REPO_ROOT = Path(__file__).resolve().parents[3]     # repo root
load_dotenv(BACKEND_ROOT / ".env")


def _ensure_toolchains_on_path() -> None:
    """Add well-known toolchain install dirs to PATH for THIS process (and every
    subprocess it spawns). Installers — notably the .NET SDK on Windows — often
    don't update PATH, so `dotnet`/`node` invocations across the agents (testing
    sandbox, dev-agent shell `run_command`, security scans) fail with WinError 2 /
    'not recognized'. Centralising it here fixes every subprocess path at once,
    rather than patching each call site. Existing PATH entries always win."""
    candidates = [
        r"C:\Program Files\dotnet",
        r"C:\Program Files (x86)\dotnet",
        os.path.expanduser(r"~\.dotnet"),
        "/usr/local/share/dotnet",
        "/usr/share/dotnet",
        os.path.expanduser("~/.dotnet"),
    ]
    path_val = os.environ.get("PATH", "")
    existing = {p.rstrip(os.sep).lower() for p in path_val.split(os.pathsep) if p}
    to_add = [
        d for d in candidates
        if os.path.isdir(d) and d.rstrip(os.sep).lower() not in existing
    ]
    if to_add:
        os.environ["PATH"] = os.pathsep.join([path_val, *to_add]) if path_val else os.pathsep.join(to_add)


_ensure_toolchains_on_path()

AGENTIC_BASE_URL = os.environ["AGENTIC_BASE_URL"]
AGENTIC_INTERNAL_BASE_URL = os.environ.get("AGENTIC_INTERNAL_BASE_URL", AGENTIC_BASE_URL)
AGENTIC_WS_URL = os.environ["AGENTIC_WS_URL"]

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_API_KEY_DESIGN = os.environ.get("GOOGLE_API_KEY_design", GOOGLE_API_KEY)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", OPENAI_API_KEY)
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

AGENTIC_APP_PATH = os.environ.get("AGENTIC_APP_PATH", str(BACKEND_ROOT))

ADO_ORG_URL = os.environ.get("ADO_ORG_URL", "")
ADO_PAT = os.environ.get("ADO_PAT", "")
DEV_WORKSPACE_ROOT: str = os.environ.get(
    "DEV_WORKSPACE_ROOT", str(BACKEND_ROOT / "files" / "dev-workspace")
)
# Where the Documentation agent saves generated docs (local stand-in for Azure Blob).
DOCS_OUTPUT_ROOT: str = os.environ.get(
    "DOCS_OUTPUT_ROOT", str(BACKEND_ROOT / "files" / "generated-docs")
)

# Anthropic Claude
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Model selection by task — use the right model for the job
# Fast: routing, document parsing, PR descriptions, simple extraction
# Standard: requirements analysis, code generation, structured reasoning
# Extended: design generation — same model but extended thinking enabled in the call
ANTHROPIC_MODEL_FAST     = os.environ.get("ANTHROPIC_MODEL_FAST",     "claude-haiku-4-5-20251001")
ANTHROPIC_MODEL_STANDARD = os.environ.get("ANTHROPIC_MODEL_STANDARD", "claude-sonnet-4-6")
ANTHROPIC_MODEL_EXTENDED = os.environ.get("ANTHROPIC_MODEL_EXTENDED", "claude-sonnet-4-6")

# MCP (Model Context Protocol) — user-registered servers consumed as agent tools.
# MCP_ENABLED gates the whole feature (registry router + per-stage tool injection).
MCP_ENABLED = os.environ.get("MCP_ENABLED", "false").lower() in ("1", "true", "yes")
# stdio transport spawns a subprocess on the backend host — off by default; only enable
# in trusted/single-tenant deployments. Commands are still checked against the allowlist.
MCP_STDIO_ENABLED = os.environ.get("MCP_STDIO_ENABLED", "false").lower() in ("1", "true", "yes")
# Comma-separated executable allowlist for stdio servers (e.g. "npx,uvx,python").
# Empty → no stdio command is permitted even when MCP_STDIO_ENABLED is true.
MCP_STDIO_COMMAND_ALLOWLIST = [
    c.strip() for c in os.environ.get("MCP_STDIO_COMMAND_ALLOWLIST", "").split(",") if c.strip()
]
# Bounded timeouts so a slow/unreachable MCP server never hangs a stage.
MCP_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("MCP_CONNECT_TIMEOUT_SECONDS", "20"))
MCP_TOOL_TIMEOUT_SECONDS = float(os.environ.get("MCP_TOOL_TIMEOUT_SECONDS", "60"))

# Testing agent — Selenium Chrome runs visibly by default (local dev); set true on
# servers/enterprise deploys where no display is available for a popped browser window.
TESTING_AGENT_HEADLESS: bool = os.environ.get("TESTING_AGENT_HEADLESS", "false").lower() in ("1", "true", "yes")

# M9.2: per-tenant LLM cost budget (REQ-M9-09).
# LLM_TENANT_BUDGET_USD_DEFAULT — default monthly-window USD budget applied to
# every tenant unless overridden. 0 means "no budget configured" — GET /cost
# returns utilization=0, breached_80=False (no divide-by-zero / always-breach).
LLM_TENANT_BUDGET_USD_DEFAULT = float(os.environ.get("LLM_TENANT_BUDGET_USD_DEFAULT", "0"))
# LLM_TENANT_BUDGET_OVERRIDES — optional JSON object string mapping
# {tenant_id: budget_usd} for tenants that diverge from the default budget.
# Empty string means no overrides (default-only). A per-tenant budget DB table
# is a future enhancement, not required by REQ-M9-09.
LLM_TENANT_BUDGET_OVERRIDES_JSON = os.environ.get("LLM_TENANT_BUDGET_OVERRIDES", "")

# Hierarchical budget enforcement (0032): org⊇workspace⊇project monthly caps enforced by
# shared.services.budget_guard at run-start + each model resolution. When a spend read
# fails (Redis/DB down) the guard FAILS OPEN by default (a monitoring outage must not
# block every run); set true to fail CLOSED (reject when spend can't be confirmed).
BUDGET_ENFORCE_FAIL_CLOSED: bool = os.environ.get("BUDGET_ENFORCE_FAIL_CLOSED", "false").lower() == "true"
# Default monthly USD budgets per scope (0032). Used both as the value stamped on a
# NEW org/workspace/project and as the EFFECTIVE cap when a scope has no explicit budget
# — so hierarchical allocation limits apply even to rows created before budgets existed.
# org ⊇ workspace ⊇ project, so defaults nest: 100 ⊇ 50 ⊇ 25.
DEFAULT_ORG_BUDGET_USD: float = float(os.environ.get("DEFAULT_ORG_BUDGET_USD", "100"))
DEFAULT_WORKSPACE_BUDGET_USD: float = float(os.environ.get("DEFAULT_WORKSPACE_BUDGET_USD", "50"))
DEFAULT_PROJECT_BUDGET_USD: float = float(os.environ.get("DEFAULT_PROJECT_BUDGET_USD", "25"))

# PostgreSQL — asyncpg URL for SQLAlchemy async engine (postgresql+asyncpg://user:pass@host:5432/dbname)
# Use POSTGRES_MIGRATIONS_CONN_STRING for Alembic — it must use a superuser/BYPASSRLS role.
# POSTGRES_MIGRATIONS_CONN_STRING defaults to "" (not POSTGRES_CONN_STRING) — the migration runner
# is the hard-required gate that enforces the superuser-DSN requirement (D-09, Pitfall 2).
POSTGRES_CONN_STRING = os.environ.get("POSTGRES_CONN_STRING", "")
POSTGRES_MIGRATIONS_CONN_STRING = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING", "")

# Redis — redis-py asyncio URL (redis://host:6379/db)
REDIS_URL = os.environ.get("REDIS_URL", "")

# Azure Blob Storage account URL (https://<account>.blob.core.windows.net)
AZURE_BLOB_ACCOUNT_URL = os.environ.get("AZURE_BLOB_ACCOUNT_URL", "")

# Azure Key Vault URL (https://<vault-name>.vault.azure.net)
AZURE_KEY_VAULT_URL = os.environ.get("AZURE_KEY_VAULT_URL", "")

# Fernet key (urlsafe-base64, 32 bytes) for the DB secret-store backend used when
# Azure Key Vault is not configured (local dev). Generate: Fernet.generate_key().decode()
SECRET_STORE_KEY: str = os.environ.get("SECRET_STORE_KEY", "")

# "local"  — MemorySaver fallback allowed (development default)
# "enterprise" — SQL checkpointing required; startup fails if unavailable
AGENT_RUNTIME_MODE = os.environ.get("AGENT_RUNTIME_MODE", "local")

# Key Vault secret NAMES for the three Postgres DSNs. The secret VALUES live in Key Vault;
# only the names are configured here. Defaults reproduce the historical
# sdlc-{env}-postgres-*-conn-string convention (env derived from AGENT_RUNTIME_MODE,
# local→dev) so existing deployments need no .env change. Override per-environment in .env
# when a vault uses different secret names. These are the single source of truth — the
# db/checkpoint/migrations modules import these constants rather than rebuilding the names.
_KV_DSN_ENV = AGENT_RUNTIME_MODE if AGENT_RUNTIME_MODE != "local" else "dev"
KV_SECRET_POSTGRES_CONN = os.environ.get(
    "KV_SECRET_POSTGRES_CONN", f"sdlc-{_KV_DSN_ENV}-postgres-conn-string")
KV_SECRET_POSTGRES_SYNC_CONN = os.environ.get(
    "KV_SECRET_POSTGRES_SYNC_CONN", f"sdlc-{_KV_DSN_ENV}-postgres-sync-conn-string")
KV_SECRET_POSTGRES_MIGRATIONS_CONN = os.environ.get(
    "KV_SECRET_POSTGRES_MIGRATIONS_CONN", f"sdlc-{_KV_DSN_ENV}-postgres-migrations-conn-string")

# ── M2: PostgresCheckpointer (psycopg3 format — distinct from POSTGRES_CONN_STRING) ──
# psycopg3 format (postgresql://) for PostgresSaver — DO NOT use POSTGRES_CONN_STRING (that is asyncpg format)
POSTGRES_SYNC_CONN_STRING: str = os.environ.get(
    "POSTGRES_SYNC_CONN_STRING",
    "postgresql://sdlc:sdlcdev@localhost:5432/sdlc_agentic",
)

# ── M2: JWT Authentication (D-01, D-02) ──
JWT_SECRET_KEY: str = os.environ.get("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
OIDC_ISSUER_URL: str = os.environ.get("OIDC_ISSUER_URL", "")
# ── M7.1: Audience validation for RS256 enterprise mode (REQ-M7-02) ──
# Must be set when AGENT_RUNTIME_MODE=enterprise and OIDC_ISSUER_URL is set.
AUTH0_AUDIENCE: str = os.environ.get("AUTH0_AUDIENCE", "")

# ── RBAC catalogue boot guard ──
# roles/permissions/role_permissions are GLOBAL tables with no RLS, so a direct
# INSERT escalates every holder of a role in every tenant. The boot check compares
# them to shared/authz/catalog.py and refuses to start on any difference.
# Set true ONLY to reconcile automatically — that silently undoes tampering, which
# is exactly the alarm the check exists to raise.
RBAC_CATALOG_AUTOREPAIR: bool = os.environ.get("RBAC_CATALOG_AUTOREPAIR", "false").lower() == "true"

# ── Single-organization bootstrap (local auth) ──
# The platform hosts exactly ONE organization; nothing creates a second one. It is
# seeded at startup if absent, and the env-listed admins are bound to it as org_admin.
# Self-serve signup joins this same org with no role bindings at all.
DEFAULT_ORG_SLUG: str = os.getenv("DEFAULT_ORG_SLUG", "pwc").strip().lower()
DEFAULT_ORG_NAME: str = os.getenv("DEFAULT_ORG_NAME", "PwC").strip()

# Comma-separated emails + initial password for the org admin(s). PLATFORM_ADMIN_* are
# the historical names for the same two values, honoured so an existing .env keeps
# working; ORG_ADMIN_* wins when both are set.
ORG_ADMIN_EMAILS = [
    e.strip().lower()
    for e in (os.getenv("ORG_ADMIN_EMAILS") or os.getenv("PLATFORM_ADMIN_EMAILS", "")).split(",")
    if e.strip()
]
ORG_ADMIN_PASSWORD = os.getenv("ORG_ADMIN_PASSWORD") or os.getenv("PLATFORM_ADMIN_PASSWORD", "")

# ── M2: LiteLLM Gateway (D-06, D-07, D-10) ──
LITELLM_BASE_URL: str = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY: str = os.environ.get("LITELLM_API_KEY", "")
ENABLE_LITELLM: bool = os.environ.get("ENABLE_LITELLM", "false").lower() == "true"

# ── M3: Worker Pool (REQ-M3-01, REQ-M3-03) ──
ENABLE_WORKER_POOL: bool = os.environ.get("ENABLE_WORKER_POOL", "false").lower() == "true"
WORKER_POOL_CONCURRENCY: int = int(os.environ.get("WORKER_POOL_CONCURRENCY", "2"))
WORKER_RECLAIM_TIMEOUT_MS: int = int(os.environ.get("WORKER_RECLAIM_TIMEOUT_MS", "60000"))

SLA_REQUIREMENTS_HOURS: int = int(os.environ.get("SLA_REQUIREMENTS_HOURS", "24"))
SLA_DESIGN_HOURS: int = int(os.environ.get("SLA_DESIGN_HOURS", "48"))
SLA_DEVELOPMENT_HOURS: int = int(os.environ.get("SLA_DEVELOPMENT_HOURS", "72"))
SLA_TESTING_HOURS: int = int(os.environ.get("SLA_TESTING_HOURS", "24"))
SLA_GRACE_MINUTES: int = int(os.environ.get("SLA_GRACE_MINUTES", "5"))
# M10.2: within-agent clarification SLA — deliberately shorter than the
# inter-agent phase gates (24/48/72h) because a within-agent question
# blocks an in-flight agent turn (REQ-M10-04).
SLA_CLARIFICATION_HOURS: int = int(os.environ.get("SLA_CLARIFICATION_HOURS", "4"))

# ── Langfuse LLM Observability (self-hosted OSS) ──
# ENABLE_LANGFUSE gates all tracing: when false, build_agent_callbacks returns only
# the AuditCallbackHandler and get_langfuse_client() returns None (zero behavior change).
# Keys + host are read by the langfuse SDK singleton (shared/observability) AND by the
# read-only traces_router (shared/routers/traces.py) which proxies the Langfuse Public API.
ENABLE_LANGFUSE: bool = os.environ.get("ENABLE_LANGFUSE", "false").lower() == "true"
LANGFUSE_HOST: str = os.environ.get("LANGFUSE_HOST", "http://localhost:3100")
LANGFUSE_PUBLIC_KEY: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.environ.get("LANGFUSE_SECRET_KEY", "")

# ── M7.3: OIDC SSO master flag (REQ-M7-15) — gates decode_token dispatch + frontend login in lockstep ──
ENABLE_OIDC: bool = os.environ.get("ENABLE_OIDC", "false").lower() == "true"
# Active OIDC provider key — must be one of OIDC_PROVIDERS.keys() (providers.py).
# Drives the nested→flat claim-extraction path AND the bounded provider metric label
# (WR-03). Single configured IdP per deployment; validated against OIDC_PROVIDERS at boot.
OIDC_PROVIDER: str = os.environ.get("OIDC_PROVIDER", "entra")

# ── M6: Webhook Pipeline (REQ-M6-11) ──
ENABLE_WEBHOOK_TRIGGERS: bool = os.environ.get("ENABLE_WEBHOOK_TRIGGERS", "false").lower() == "true"
# Connector credential Key Vault secret names (convention only; values loaded via load_secret())
# Jira:  load_secret("jira-email"), load_secret("jira-api-token"), load_secret("jira-url")
# GitHub: load_secret("github-app-id"), load_secret("github-app-private-key"),
#         load_secret("github-app-installation-id")
# Slack:  load_secret("slack-bot-token")
# Azure Repos: reuses ADO_ORG_URL + load_secret("ado-pat")
# GitHub Actions: secret_store refs gha-pat / gha-owner (written by the Integrations
#         "Add credentials" form) — see shared/routers/connectors.py.
# MS Teams + SharePoint: ONE Entra app registration per tenant, shared by both kinds.
#         secret_store refs msgraph-tenant-id / msgraph-client-id / msgraph-client-secret,
#         plus non-secret routing refs msteams-team-id, msteams-channel-id,
#         msteams-webhook-url, sharepoint-site-id, sharepoint-drive-id,
#         sharepoint-folder-path.
# Env-var fallbacks for local dev (never use in production)
JIRA_URL:        str = os.environ.get("JIRA_URL", "")
JIRA_EMAIL:      str = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN:  str = os.environ.get("JIRA_API_TOKEN", "")
GITHUB_APP_ID:   str = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_INSTALLATION_ID: str = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
# Local-dev fallbacks for the GitHub App private key (KV `github-app-private-key` wins).
# Provide EITHER the raw PEM in GITHUB_APP_PRIVATE_KEY or a filesystem path in
# GITHUB_APP_PRIVATE_KEY_PATH. Never use these in production — use Key Vault.
GITHUB_APP_PRIVATE_KEY:      str = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_PRIVATE_KEY_PATH: str = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
SLACK_BOT_TOKEN: str = os.environ.get("SLACK_BOT_TOKEN", "")
# GitHub Actions — the per-tenant secret store is authoritative; these are local-dev
# fallbacks only, matching the tail of every connector's auth ladder.
GHA_PAT:   str = os.environ.get("GHA_PAT", "")
GHA_OWNER: str = os.environ.get("GHA_OWNER", "")
# Microsoft Graph app registration (client-credentials flow) — serves BOTH the
# ms_teams and sharepoint connectors. Local-dev fallbacks; the per-tenant secret
# store written by the Integrations form takes precedence.
MSGRAPH_TENANT_ID:     str = os.environ.get("MSGRAPH_TENANT_ID", "")
MSGRAPH_CLIENT_ID:     str = os.environ.get("MSGRAPH_CLIENT_ID", "")
MSGRAPH_CLIENT_SECRET: str = os.environ.get("MSGRAPH_CLIENT_SECRET", "")

# Webhook signature secrets (REQ-M6-06) — verify inbound webhook signatures.
# Loaded KV-first in the FastAPI lifespan, then these env vars as local-dev fallback.
# KV secret names: github-webhook-secret, slack-signing-secret, jira-webhook-secret,
#                  ado-webhook-user, ado-webhook-password
# Azure DevOps service hooks have NO HMAC — they authenticate via HTTP Basic Auth,
# so the ADO pair is a username/password, not a signing key.
GITHUB_WEBHOOK_SECRET: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
SLACK_SIGNING_SECRET:  str = os.environ.get("SLACK_SIGNING_SECRET", "")
JIRA_WEBHOOK_SECRET:   str = os.environ.get("JIRA_WEBHOOK_SECRET", "")
ADO_WEBHOOK_USER:      str = os.environ.get("ADO_WEBHOOK_USER", "")
ADO_WEBHOOK_PASSWORD:  str = os.environ.get("ADO_WEBHOOK_PASSWORD", "")
# GitHub Actions workflow_run deliveries are signed exactly like GitHub Issues
# (X-Hub-Signature-256, HMAC-SHA256 over the raw body) but with their own secret so a
# CI webhook can be rotated without touching the issues webhook.
GHA_WEBHOOK_SECRET:    str = os.environ.get("GHA_WEBHOOK_SECRET", "")
# Microsoft Graph change notifications carry NO signature — clientState is the only
# authentication Graph offers, so this must be >=32 bytes of entropy and is compared
# with hmac.compare_digest. KV name: msgraph-webhook-client-state.
MSGRAPH_WEBHOOK_CLIENT_STATE: str = os.environ.get("MSGRAPH_WEBHOOK_CLIENT_STATE", "")

# ── M7.4: SCIM Provisioning (REQ-M7-16) ──
ENABLE_SCIM: bool = os.environ.get("ENABLE_SCIM", "false").lower() == "true"
# KV secret: load_secret("scim-bearer-token", tenant_id=tid) — per-tenant SCIM credential

# ── M7.4: Jira OAuth 3LO (REQ-M7-20) ──
# KV secrets written at callback: jira-access-token, jira-refresh-token, jira-cloud-id (tenant-scoped)
JIRA_OAUTH_CLIENT_ID: str = os.environ.get("JIRA_OAUTH_CLIENT_ID", "")
JIRA_OAUTH_CLIENT_SECRET: str = os.environ.get("JIRA_OAUTH_CLIENT_SECRET", "")
GITHUB_OAUTH_CLIENT_SECRET: str = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "")
SLACK_CLIENT_ID: str = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET: str = os.environ.get("SLACK_CLIENT_SECRET", "")
