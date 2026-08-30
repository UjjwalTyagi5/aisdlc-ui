"""Central environment loader for agentic_app.

Import constants from here — do not call os.getenv elsewhere.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]  # platform/backend
REPO_ROOT = Path(__file__).resolve().parents[3]     # repo root
load_dotenv(BACKEND_ROOT / ".env")

# ── where secrets come from ──────────────────────────────────────────────────
# ENV=dev (the default) reads everything below from .env, exactly as it always has.
# Any other value means the secrets live in Azure Key Vault, and this call writes them
# into os.environ BEFORE the first constant is read — which is why none of the
# os.environ.get(...) lines in this file needed to change.
#
# ORDER IS LOAD-BEARING: this must run after load_dotenv (it needs AZURE_KEY_VAULT_URL
# and ENV, both of which come from .env) and before anything reads a setting. Moving it
# below a constant means that constant silently keeps its .env value in production.
#
# It raises rather than falling back when the vault is required and unreachable. See
# config/secret_bootstrap.py for why a refusal to boot is the safe failure here.
from config.secret_bootstrap import current_env, hydrate_environment  # noqa: E402

ENV: str = current_env()
hydrate_environment()


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

# NO OPENAI_API_KEY HERE, deliberately: LiteLLM reads it as an implicit fallback, so a
# tenant call with no resolved credential would silently bill the platform's account.
# Models are BYOK — the key comes from the tenant's own model_providers row via
# model_resolver.

AGENTIC_APP_PATH = os.environ.get("AGENTIC_APP_PATH", str(BACKEND_ROOT))

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
# There are no default budgets. A scope with no explicit budget has NO cap — see
# shared/services/budget_alloc.py:_org_cap.

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
# PLATFORM vault: secrets the app only ever READS (JWT signing key, Redis URL, webhook
# secrets). The app's identity holds "Key Vault Secrets User" here — read-only, so a
# code-execution bug cannot replace a platform credential.
AZURE_KEY_VAULT_URL = os.environ.get("AZURE_KEY_VAULT_URL", "")

# TENANT vault: per-tenant credentials the app must both READ and WRITE — BYOK model
# keys ({tenant}-model-{provider_id}) and connector/MCP secrets. Split from the platform
# vault by TRUST, not by name prefix: writing these requires "Key Vault Secrets Officer",
# and that grant must not extend to jwt-secret-key.
#
# Falls back to the platform vault when unset, so an existing single-vault deployment
# keeps working unchanged until it provisions the second vault.
AZURE_TENANT_VAULT_URL = os.environ.get("AZURE_TENANT_VAULT_URL", "") or AZURE_KEY_VAULT_URL

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

# Comma-separated emails + initial password for the org admin(s).
ORG_ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.getenv("ORG_ADMIN_EMAILS", "").split(",")
    if e.strip()
]
ORG_ADMIN_PASSWORD = os.getenv("ORG_ADMIN_PASSWORD", "")

# ── M2: LiteLLM Gateway (D-06, D-07, D-10) ──
LITELLM_BASE_URL: str = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY: str = os.environ.get("LITELLM_API_KEY", "")
ENABLE_LITELLM: bool = os.environ.get("ENABLE_LITELLM", "false").lower() == "true"

# ── M3: Worker Pool (REQ-M3-01, REQ-M3-03) ──
ENABLE_WORKER_POOL: bool = os.environ.get("ENABLE_WORKER_POOL", "false").lower() == "true"
WORKER_RECLAIM_TIMEOUT_MS: int = int(os.environ.get("WORKER_RECLAIM_TIMEOUT_MS", "60000"))

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

# ── Connector health probes ──
# process_api.py runs two background loops that re-probe every configured connector
# every 30s. Defaults ON so deployed environments keep the behaviour the Integrations
# page depends on. Turn it OFF for local dev: with unreachable or unconfigured
# credentials each probe drifts toward its own 30s timeout, so the "every 30s" cycle
# never actually idles — it saturates a core and adds ~1s to every request, because the
# probes and the request handlers share one event loop.
ENABLE_CONNECTOR_HEALTH_PROBES: bool = os.environ.get(
    "ENABLE_CONNECTOR_HEALTH_PROBES", "true"
).lower() in ("1", "true", "yes")

# ── M6: Webhook Pipeline (REQ-M6-11) ──
ENABLE_WEBHOOK_TRIGGERS: bool = os.environ.get("ENABLE_WEBHOOK_TRIGGERS", "false").lower() == "true"

# ── Connector credentials: NOT HERE, AT ALL ─────────────────────────────────
# Every connector value is per-tenant — credentials, OAuth client secrets, inbound
# webhook signing secrets, and the site/org URLs that pair with them. They are written
# by the Integrations "Add credentials" form into the tenant secret store and read back
# through two tenant-scoped rungs, with no global-vault rung and no env-var rung:
#
#     secret_store(tenant, ref)  ->  Key Vault "{tenant}-{ref}"
#
# A tenant with no credential of its own fails cleanly as "not connected" rather than
# borrowing the platform's. That is the whole point: a shared value let a tenant who
# never connected Jira transact with somebody else's token, and let one webhook secret
# verify a delivery to every tenant's URL.
#
# Enforced, not just documented:
#   tests/test_connector_platform_fallback.py  — no connector may read config.env,
#       resolve an untenanted secret, or take a credential in its constructor.
#   webhooks/router.py:_tenant_webhook_secret  — inbound secrets resolve per tenant.

# ── M7.4: SCIM Provisioning (REQ-M7-16) ──
ENABLE_SCIM: bool = os.environ.get("ENABLE_SCIM", "false").lower() == "true"
# KV secret: load_secret("scim-bearer-token", tenant_id=tid) — per-tenant SCIM credential

# ── Outbound email (onboarding invites + password reset) ──
#
# Provider-agnostic on purpose: every candidate — Gmail, Azure Communication Services,
# SES, SendGrid, Mailgun — speaks SMTP, so moving between them is configuration and never
# code. Gmail works for development with an App Password (2FA required on the account;
# plain-password SMTP auth was removed by Google), but is a poor production choice for
# THIS traffic: the From address cannot be your own domain, there is no SPF/DKIM/DMARC
# alignment and so a real spam-folder risk, and a password-setup link that lands in spam
# means the user cannot get in at all.
#
# UNSET IS A SUPPORTED STATE. With no SMTP_HOST the sender logs the message instead of
# delivering it, so local development and the test suite need no mail server. It is
# logged at WARNING, not INFO, because an email that silently did not send is the kind of
# thing that should be visible.
SMTP_HOST: str = os.environ.get("SMTP_HOST", "")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
# STARTTLS on 587 (the common case, including Gmail); implicit TLS on 465.
SMTP_USE_TLS: bool = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL: bool = os.environ.get("SMTP_USE_SSL", "false").lower() == "true"
EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "")
EMAIL_FROM_NAME: str = os.environ.get("EMAIL_FROM_NAME", "SDLC Platform")

# Where the links in those emails point — the BROWSER-facing origin, which is not
# AGENTIC_BASE_URL (that is this API). A set-password link built against the API host is
# a link to nothing, so this is separate rather than derived.
PUBLIC_APP_URL: str = os.environ.get("PUBLIC_APP_URL", "http://localhost:3000")

# How long a set-password / reset link stays valid.
#
# MINUTES, NOT HOURS. These were INVITE_TOKEN_TTL_HOURS=48 / RESET_TOKEN_TTL_HOURS=2, and
# an hours-based setting cannot express a ten-minute link at all — the finest it can say
# is "1", which is six times too long. The unit is the whole point of the knob, so it
# moved rather than gaining a second parallel setting.
#
# A live token IS a credential (see shared/services/password_setup.py), so a short life is
# the safer default and ten minutes is the usual figure for a reset: it is requested
# deliberately and acted on straight away.
#
# THE INVITE IS THE ONE TO WATCH. It goes to somebody who is NOT expecting it and may not
# be at their desk, and it is the only way into an account that has no password yet. At
# ten minutes, any invite not opened almost immediately is dead and needs an admin to
# resend. That is a deliberate choice here, not an oversight — and it is why this is an
# environment variable: raising the invite alone needs no code change and no deploy.
INVITE_TOKEN_TTL_MINUTES: int = int(os.environ.get("INVITE_TOKEN_TTL_MINUTES", "10"))
RESET_TOKEN_TTL_MINUTES: int = int(os.environ.get("RESET_TOKEN_TTL_MINUTES", "10"))
