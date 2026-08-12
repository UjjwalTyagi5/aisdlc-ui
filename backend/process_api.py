import asyncio
import json
import logging
import sys
import time
from contextlib import asynccontextmanager

# Force UTF-8 on stdout/stderr so logging/printing non-ASCII (e.g. "→" in agent
# progress messages) never raises UnicodeEncodeError on a Windows cp1252 console —
# that crash was breaking the Testing agent's WebSocket stream mid-run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

import jwt as _pyjwt
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
# Importing routers for chat and development functionalities
from agents_orchestrator.orchestrator.orchestrator_api import orchestrator_router
from agents_orchestrator.orchestrator.copilot_api import copilot_router
from agents_orchestrator.requirements_agent.requirements_agent_api import requirement_router_orchestrator
from agents_orchestrator.design_architecture_agent.design_architecture_agent_api import design_router_orchestrator
from agents_orchestrator.development_agent.development_agent_api import development_router_orchestrator
from agents_orchestrator.code_review_agent.code_review_agent_api import code_review_router
from agents_orchestrator.security_agent.security_agent_api import security_router
from agents_orchestrator.deployment_agent.deployment_agent_api import deployment_router_orchestrator
from agents_orchestrator.testing_agent.testing_agent_api import testing_router_orchestrator
from config.connectors.router import connectors_router

import os
from config import sdlcSettings
from config.auth.jwt import (
    decode_token as _decode_token,
    is_enterprise_oidc as _is_enterprise_oidc_gate,
    JWKSUnavailableError as _JWKSUnavailableError,
)
from config.auth.ws_ticket import mint_ws_ticket as _mint_ws_ticket
from config.checkpoint import get_checkpointer_status
from config.env import (
    AGENT_RUNTIME_MODE,
    JWT_SECRET_KEY,
    POSTGRES_CONN_STRING,
    REDIS_URL,
    AZURE_BLOB_ACCOUNT_URL,
    ENABLE_LITELLM,
    LITELLM_BASE_URL,
    ENABLE_WORKER_POOL,
    ENABLE_TEMPORAL,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    ENABLE_WEBHOOK_TRIGGERS,
    GITHUB_WEBHOOK_SECRET,
    SLACK_SIGNING_SECRET,
    JIRA_WEBHOOK_SECRET,
    ADO_WEBHOOK_USER,
    ADO_WEBHOOK_PASSWORD,
    GHA_WEBHOOK_SECRET,
    MSGRAPH_WEBHOOK_CLIENT_STATE,
    ENABLE_OIDC,
    ENABLE_SCIM,
    AUTH0_AUDIENCE,
    OIDC_ISSUER_URL,
    OIDC_PROVIDER,
)
from shared.auth.denylist import is_jti_denied
from config.auth.providers import extract_tenant_id, OIDC_PROVIDERS, resolve_provider_key
from shared.authz.dependency import assert_all_routes_protected, public, require_permission
from shared.auth.platform_identity import seed_platform_admins
from shared.authz.resolver import resolve_permissions_for_user, PermissionResolutionError
from shared.db import engine, get_db_session_for_tenant, get_db_session_superuser, RESOLVED_POSTGRES_CONN_STRING
from shared.models.orm import Organization, Run
from shared.services.metrics import OIDC_AUTH_ATTEMPTS
from shared.routers.projects import projects_router
from shared.routers.workspaces import workspaces_router
from shared.routers.runs import runs_router
from shared.routers.audit import audit_router, audit_runs_router
from shared.routers.eval import eval_runs_router
from shared.routers.cost import cost_router
from shared.routers.traces import traces_router
from shared.routers.artifacts import artifacts_router
from shared.routers.connectors import connectors_resource_router
from shared.routers.evidence import evidence_router
from shared.routers.signals import signals_router
from shared.routers.admin import admin_router
from shared.routers.custom_roles import custom_roles_router
from shared.routers.auth_local import auth_local_router
from shared.routers.platform import platform_router
from shared.routers.model import model_router, model_options_router
from shared.routers.capabilities import capabilities_router
from shared.routers.conversations import conversations_router
from shared.services.artifact_service import _ARTIFACT_CHANNEL
from shared.storage.azure_blob import BlobStorageClient
from shared.keyvault import load_secret

logger = logging.getLogger(__name__)

esett = sdlcSettings()


def _check_oidc_audience_guard() -> None:
    """Fail boot if enterprise OIDC mode is mis-configured (CR-01, CR-02).

    Called once at lifespan startup (beside the BYPASSRLS guard) so a missing
    configuration is caught at boot, not silently at the first token validation.

    Three fail-closed checks for an ENABLE_OIDC + enterprise deployment:
      1. OIDC_ISSUER_URL MUST be set (CR-01). Without it, decode_token falls through
         to the HS256 path while the middleware would still enter the OIDC branch on
         the OLD gate — a tenant-spoofing window. Requiring it at boot makes the
         decode gate and middleware gate (both is_enterprise_oidc()) agree by
         construction: an OIDC-enabled enterprise app cannot silently fall back to HS256.
      2. OIDC_ISSUER_URL MUST be https:// (CR-02). Signing keys are fetched from the
         issuer; an http:// issuer is a key-substitution / MITM path.
      3. AUTH0_AUDIENCE MUST be set (existing SC#3). An absent audience disables aud
         validation entirely — any RS256 token from the same IdP would be accepted.
    """
    if not (ENABLE_OIDC and AGENT_RUNTIME_MODE == "enterprise"):
        return
    if not OIDC_ISSUER_URL:
        raise RuntimeError(
            "OIDC_ISSUER_URL is required in enterprise OIDC mode (CR-01). "
            "Without it decode_token silently falls back to HS256 while the middleware "
            "enters the OIDC branch — a tenant-spoofing window. "
            "Set OIDC_ISSUER_URL to the IdP issuer (Auth0 = https://<tenant>.auth0.com/)."
        )
    if not OIDC_ISSUER_URL.rstrip("/").startswith("https://"):
        raise RuntimeError(
            "OIDC_ISSUER_URL must be https:// in enterprise OIDC mode (CR-02) — "
            "refusing to fetch JWKS signing keys over an insecure channel."
        )
    if not AUTH0_AUDIENCE:
        raise RuntimeError(
            "AUTH0_AUDIENCE is required in enterprise OIDC mode (SC#3). "
            "Set AUTH0_AUDIENCE to the FastAPI API identifier registered in Auth0. "
            "See REQ-M7-13/SC#3 and D-03."
        )


async def _tenant_is_registered(tenant_id: str) -> bool:
    """Return True if tenant_id matches a row in the organizations registry.

    Runs BEFORE the tenant RLS GUC is set (the registry lookup is pre-tenant-context).
    WHY get_db_session_superuser: organizations carries no RLS policy (not in
    _RLS_TABLES per migration 0001; only projects/runs/artifacts/audit_events/
    agent_call_logs are RLS-forced) — so this read is safe without a tenant GUC.
    Generic 401 on miss — never reveal which tenants exist (T-7.3-07).
    """
    async with get_db_session_superuser() as session:
        row = await session.execute(
            select(Organization.id).where(Organization.id == tenant_id)
        )
        return row.scalar_one_or_none() is not None


def _env_list(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


cors_allowed_origins = _env_list(
    "AGENTIC_CORS_ALLOWED_ORIGINS",
    os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"),
)
cors_allowed_origin_regex = os.getenv(
    "AGENTIC_CORS_ALLOWED_ORIGIN_REGEX",
    os.getenv("CORS_ALLOWED_ORIGIN_REGEXES", r"^https://.*\.ngrok-free\.app$"),
).strip() or None


async def _probe_postgres(conn_string: str) -> str:
    if not conn_string:
        return "not configured"
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        engine = create_async_engine(conn_string, pool_size=1, max_overflow=0)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


async def _probe_redis(redis_url: str) -> str:
    if not redis_url:
        return "not configured"
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


async def _probe_litellm(litellm_base_url: str) -> str:
    if not litellm_base_url:
        return "not configured"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{litellm_base_url}/health/liveliness")
            return "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


async def _probe_blob(blob_client: BlobStorageClient) -> str:
    if blob_client is None:
        return "not configured"
    try:
        await blob_client._client.get_service_properties()
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


# Maps artifact_type to the next pipeline stage — allowlist prevents arbitrary
# stage transitions from spoofed Redis pub/sub messages (T-M2-06-01).
_ORCHESTRATOR_ROUTING = {
    "requirements": "design",
    "design": "development",
    "development": "testing",
    "testing": "deployment",
}


async def _handle_artifact_ready(payload: dict) -> None:
    """Update runs.current_stage and gate_pending when an artifact_ready event arrives."""
    run_id = payload.get("run_id")
    artifact_type = payload.get("artifact_type")
    tenant_id = payload.get("tenant_id")
    next_stage = _ORCHESTRATOR_ROUTING.get(artifact_type)
    # Fail-closed: if tenant_id is absent we cannot scope the session to the right
    # tenant — do NOT fall back to a NULL-tenant write (T-M7.1-09 mitigation).
    if not run_id or not next_stage or not tenant_id:
        return
    async with get_db_session_for_tenant(tenant_id) as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if run:
            run.current_stage = next_stage
            run.gate_pending = True
    logger.info(
        "Orchestrator: run %s advanced to stage %s (gate_pending=True)", run_id, next_stage
    )


async def _artifact_event_listener() -> None:
    """Subscribe to Redis artifact_events channel and route pipeline stage transitions."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL)
    pubsub = client.pubsub()
    await pubsub.subscribe(_ARTIFACT_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                try:
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    payload = json.loads(data)
                    await _handle_artifact_ready(payload)
                except Exception as exc:
                    logger.warning("artifact_event_listener: error handling message: %s", exc)
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(_ARTIFACT_CHANNEL)
        await client.aclose()


async def _refresh_health(app: FastAPI) -> None:
    """Background coroutine — re-probes all infra every 30 seconds."""
    _HEALTH_TTL_SECONDS = 30
    while True:
        blob_client = getattr(app.state, "blob_client", None)
        async def _noop_litellm():
            return "disabled"

        postgres, redis_status, blob, litellm_status = await asyncio.gather(
            _probe_postgres(RESOLVED_POSTGRES_CONN_STRING),
            _probe_redis(REDIS_URL),
            _probe_blob(blob_client),
            _probe_litellm(LITELLM_BASE_URL) if ENABLE_LITELLM else _noop_litellm(),
            return_exceptions=True,
        )
        app.state.health_cache = {
            "postgres": str(postgres),
            "redis": str(redis_status),
            "blob": str(blob),
            "litellm": str(litellm_status),
            "django": "removed",
            "probed_at": time.time(),
        }
        await asyncio.sleep(_HEALTH_TTL_SECONDS)


def _build_connectors_for_health_probe():
    """Instantiate every registered connector for the health probe loop.

    Each connector is constructed without credentials — credentials are resolved
    ephemerally inside health_check() via auth_adapter(). Instantiation is
    synchronous and cheap (no I/O at construction time).

    Keep this list in sync with config.connectors.router._EXPECTED_CONNECTOR_NAMES:
    a name expected there but missing here (or whose probe raises, since
    _probe_all_connectors drops exceptions) makes GET /connectors/health fail its
    subset test and re-probe inline on every request.

    Returns a list of connector instances.
    """
    from config.connectors.azure_devops import AzureDevOpsConnector
    from config.connectors.jira import JiraConnector
    from config.connectors.github_issues import GitHubIssuesConnector
    from config.connectors.azure_repos import AzureReposConnector
    from config.connectors.slack import SlackConnector
    from config.connectors.github_actions import GitHubActionsConnector
    from config.connectors.msteams import MSTeamsConnector
    from config.connectors.sharepoint import SharePointConnector
    from config.env import ADO_ORG_URL, JIRA_URL

    return [
        AzureDevOpsConnector(ADO_ORG_URL),
        JiraConnector(JIRA_URL),
        GitHubIssuesConnector(),
        AzureReposConnector(ADO_ORG_URL),
        SlackConnector(),
        GitHubActionsConnector(),
        MSTeamsConnector(),
        SharePointConnector(),
    ]


async def _probe_all_connectors() -> dict:
    """Probe every registered connector concurrently and return a health cache dict.

    Uses asyncio.gather(return_exceptions=True) so one slow or failing connector
    cannot block the others (REQ-M6-11, T-m6-08-D).  Probe errors are surfaced
    as type(exc).__name__ only — never str(exc) — to avoid leaking credentials
    (T-m6-08-ID, M1 architectural decision).
    """
    connectors = _build_connectors_for_health_probe()
    results = await asyncio.gather(
        *[c.health_check() for c in connectors],
        return_exceptions=True,
    )
    cache: dict = {"probed_at": time.time()}
    for result in results:
        if isinstance(result, Exception):
            logger.warning(
                "connector health probe failed: %s", type(result).__name__
            )
        else:
            cache[result.connector_name] = result.model_dump()
    return cache


async def _refresh_connector_health(app) -> None:
    """Background coroutine — re-probes every registered connector every 30 seconds.

    Probes ADO, Jira, GitHub Issues, Azure Repos, and Slack concurrently via
    asyncio.gather(return_exceptions=True).  One slow connector cannot block the
    30-second cycle (REQ-M6-11, T-m6-08-D).

    Writes app.state.connector_health_cache; GET /connectors/health reads it.
    Probe failures are logged with type(exc).__name__ only (never str(exc)) to
    avoid leaking credentials, per the M1 architectural decision (T-m6-08-ID).
    """
    _HEALTH_TTL = 30
    while True:
        app.state.connector_health_cache = await _probe_all_connectors()
        await asyncio.sleep(_HEALTH_TTL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.health_cache = {
        "postgres": "initializing",
        "redis": "initializing",
        "blob": "initializing",
        "django": "removed",
        "probed_at": 0,
    }

    # Capture the running event loop so ws_helper.broadcast_log can schedule
    # WebSocket broadcasts from the agent's worker threads (tool execution runs
    # off the main loop). Without this, MAIN_LOOP stays None and every
    # broadcast_log() falls back to a stdout-only print — the frontend never
    # receives the live activity stream (Reading/Editing/Committing/…).
    from config.ws_helper import set_main_loop
    set_main_loop(asyncio.get_running_loop())

    # Key Vault — TM1-008: attempt to read one secret at startup
    env_name = AGENT_RUNTIME_MODE if AGENT_RUNTIME_MODE != "local" else "dev"
    kv_secret = await load_secret(f"sdlc-{env_name}-postgres-conn-string")
    if kv_secret:
        logger.info("Key Vault startup read succeeded for sdlc-%s-postgres-conn-string", env_name)
    # Non-fatal if None — env vars are the fallback

    # Blob Storage — store client for health probe and artifact uploads
    if AZURE_BLOB_ACCOUNT_URL:
        app.state.blob_client = BlobStorageClient()
    else:
        app.state.blob_client = None

    # Run the FIRST probe synchronously before yield so that the very first /health
    # request gets real probe results instead of "initializing" values.
    # CRITICAL: if only a background task ran probes, /health would return "initializing"
    # for up to 30 seconds, and all_ok would falsely report True (empty iterator).
    blob_client_ref = getattr(app.state, "blob_client", None)
    postgres_result, redis_result, blob_result = await asyncio.gather(
        _probe_postgres(RESOLVED_POSTGRES_CONN_STRING),
        _probe_redis(REDIS_URL),
        _probe_blob(blob_client_ref),
        return_exceptions=True,
    )
    litellm_result = "disabled"
    if ENABLE_LITELLM:
        litellm_result = await _probe_litellm(LITELLM_BASE_URL)
        if litellm_result != "ok":
            raise RuntimeError(
                f"ENABLE_LITELLM=true but LiteLLM proxy unreachable at {LITELLM_BASE_URL}: {litellm_result}"
            )
        logger.info("LiteLLM proxy reachable at %s", LITELLM_BASE_URL)

    # Initial synchronous connector health probe — runs BEFORE the Temporal connect
    # so that GET /connectors/health returns real probe results even when Temporal
    # is unavailable.  Failures are tolerated (return_exceptions=True inside
    # _probe_all_connectors); the cache contains whatever connectors succeed.
    # This mirrors the infra _probe_postgres pattern used for the /health endpoint.
    app.state.connector_health_cache = await _probe_all_connectors()

    # Webhook signature secrets (REQ-M6-06) — read by webhooks/router.py at request
    # time to verify inbound signatures BEFORE any processing. Loaded Key-Vault-first
    # with env-var fallback, mirroring the connector auth_adapter() pattern. Set
    # unconditionally (independent of ENABLE_WEBHOOK_TRIGGERS, which only gates
    # downstream Temporal run-creation — signature verification must run on every
    # inbound webhook regardless). ADO uses HTTP Basic Auth (no HMAC), so its pair
    # is a username/password rather than a signing key.
    app.state.github_webhook_secret = await load_secret("github-webhook-secret") or GITHUB_WEBHOOK_SECRET
    app.state.slack_signing_secret = await load_secret("slack-signing-secret") or SLACK_SIGNING_SECRET
    app.state.jira_webhook_secret = await load_secret("jira-webhook-secret") or JIRA_WEBHOOK_SECRET
    app.state.ado_webhook_user = await load_secret("ado-webhook-user") or ADO_WEBHOOK_USER
    app.state.ado_webhook_password = await load_secret("ado-webhook-password") or ADO_WEBHOOK_PASSWORD
    # GitHub Actions uses the same HMAC scheme as GitHub Issues but its own secret, so
    # the CI webhook can be rotated independently.
    app.state.gha_webhook_secret = await load_secret("gha-webhook-secret") or GHA_WEBHOOK_SECRET
    # Microsoft Graph sends NO signature — clientState is the only authentication, so
    # the /webhooks/msgraph route fails closed when this is unset.
    app.state.msgraph_client_state = (
        await load_secret("msgraph-webhook-client-state") or MSGRAPH_WEBHOOK_CLIENT_STATE
    )

    # Redis connection pool for the worker pool (REQ-M3-01). Only created when
    # the feature flag is on — ENABLE_WORKER_POOL=false keeps local dev Redis-free.
    audit_retry_task = None
    if ENABLE_WORKER_POOL:
        import redis.asyncio as aioredis
        pool = aioredis.ConnectionPool.from_url(REDIS_URL, max_connections=20)
        app.state.redis_pool = pool
        logger.info("Redis connection pool created for worker pool")

    # JTI denylist Redis client — active whenever REDIS_URL is set, regardless of
    # ENABLE_SCIM flag so revocation is available from the moment tokens carry jti claims
    # (D-01, Wave A). In local dev without Redis, app.state.redis_denylist is None and
    # the denylist check is skipped (T-7.4-02: accepted residual risk for local dev).
    import redis.asyncio as aioredis  # noqa: F811 — safe re-import for clarity
    if REDIS_URL:
        app.state.redis_denylist = aioredis.from_url(REDIS_URL)
        logger.info("Redis JTI denylist client initialized")
        # AuditRetryWorker (REQ-M8-05) — drains audit:dead_letter stream.
        # Started here because it requires Redis; gated on REDIS_URL being set.
        # Task stored on app.state so the shutdown block below can cancel it cleanly.
        from workers.audit_retry_worker import AuditRetryWorker
        audit_retry_task = asyncio.create_task(AuditRetryWorker().run())
        app.state.audit_retry_task = audit_retry_task
        logger.info("AuditRetryWorker started")
    else:
        app.state.redis_denylist = None
        logger.info("REDIS_URL not set — JTI denylist disabled (local dev only)")

    # Temporal client — ONE cached connection shared across all request handlers
    # (Pitfall 3: never call Client.connect() per request — one gRPC channel per process).
    # When ENABLE_TEMPORAL=false, set to None so handlers know Temporal is unavailable.
    if ENABLE_TEMPORAL:
        from temporalio.client import Client as TemporalClient
        app.state.temporal_client = await TemporalClient.connect(
            TEMPORAL_ADDRESS,
            namespace=TEMPORAL_NAMESPACE,
        )
        logger.info(
            "Temporal client connected: address=%s namespace=%s",
            TEMPORAL_ADDRESS,
            TEMPORAL_NAMESPACE,
        )
    else:
        app.state.temporal_client = None
        logger.info("Temporal disabled (ENABLE_TEMPORAL=false) — app.state.temporal_client=None")

    app.state.health_cache = {
        "postgres": str(postgres_result),
        "redis": str(redis_result),
        "blob": str(blob_result),
        "litellm": litellm_result,
        "django": "removed",
        "probed_at": time.time(),
    }

    # Guard: refuse to start in enterprise mode if the app DB role has BYPASSRLS
    # or SUPERUSER. A BYPASSRLS app connection silently disables all RLS policies,
    # making tenant isolation structurally ineffective (Pitfall 5, T-M7.1-16).
    # In non-enterprise mode (local dev / MemorySaver stacks) this check is skipped
    # so local development without a restricted DB role is unaffected.
    if AGENT_RUNTIME_MODE == "enterprise" and RESOLVED_POSTGRES_CONN_STRING:
        async with engine.connect() as _guard_conn:
            _role_row = (
                await _guard_conn.execute(
                    text(
                        "SELECT usesuper, rolbypassrls "
                        "FROM pg_user "
                        "JOIN pg_roles ON pg_user.usename = pg_roles.rolname "
                        "WHERE pg_user.usename = current_user"
                    )
                )
            ).fetchone()
            if _role_row and (_role_row.usesuper or _role_row.rolbypassrls):
                raise RuntimeError(
                    "App DB role has SUPERUSER or BYPASSRLS — tenant isolation is disabled. "
                    "POSTGRES_CONN_STRING must point at a restricted app role (not the migrations "
                    "superuser DSN). Create a non-BYPASSRLS role and update POSTGRES_CONN_STRING. "
                    "See REQ-M7-06 / SC-06 and PITFALLS.md Pitfall 5."
                )
        logger.info(
            "BYPASSRLS startup guard: app DB role verified — not SUPERUSER, not BYPASSRLS"
        )

    # Guard: refuse to start in enterprise OIDC mode if AUTH0_AUDIENCE is absent.
    # A missing audience silently disables aud validation in PyJWT — any RS256 token
    # from the same IdP (e.g. a Microsoft Graph token) would be accepted (T-7.3-09,
    # SC#3).  Mirrors the BYPASSRLS guard above: fail-boot rather than fail at
    # first request.  Not gated to enterprise/POSTGRES_CONN_STRING — the audience
    # requirement is OIDC-specific (ENABLE_OIDC + OIDC_ISSUER_URL guard is inside
    # _check_oidc_audience_guard).
    _check_oidc_audience_guard()
    if ENABLE_OIDC and AGENT_RUNTIME_MODE == "enterprise" and OIDC_ISSUER_URL:
        logger.info("OIDC audience startup guard: AUTH0_AUDIENCE present in enterprise OIDC mode")

    # Guard: ENABLE_SCIM requires REDIS_URL in enterprise mode (T-7.4-10, fail-closed boot guard).
    # The JTI denylist is the SCIM deprovision SLA mechanism (REQ-M7-17, D-01); if Redis is
    # absent in enterprise mode the denylist silently fails-open, which is structurally
    # unacceptable for the 5-minute revocation SLA. Mirrors the BYPASSRLS / OIDC_AUDIENCE guards.
    if ENABLE_SCIM and AGENT_RUNTIME_MODE == "enterprise" and not REDIS_URL:
        raise RuntimeError(
            "ENABLE_SCIM=true in enterprise mode requires REDIS_URL to be set. "
            "The JTI denylist (REQ-M7-17/D-01) depends on Redis for the 5-minute "
            "deprovision→401 SLA. Set REDIS_URL or disable ENABLE_SCIM. (T-7.4-10)"
        )
    if ENABLE_SCIM:
        logger.info(
            "SCIM startup guard: ENABLE_SCIM=true redis_denylist=%s",
            "enabled" if getattr(app.state, "redis_denylist", None) is not None else "disabled (local)",
        )

    # Phase 3: idempotently seed env-listed platform admins (users + platform_users).
    # No-op unless PLATFORM_ADMIN_EMAILS and PLATFORM_ADMIN_PASSWORD are both set.
    await seed_platform_admins()

    # D-05 / SC-04 / T-7.2-15 boot scan: every APIRoute must be either
    # require_permission-protected, public()/allowlist-marked, or the signals
    # route's documented in-body exception — fail-open is structurally
    # impossible. Runs UNCONDITIONALLY (not gated to enterprise mode like the
    # BYPASSRLS guard above) because D-05 demands the guarantee hold in EVERY
    # runtime mode, including local dev — a forgotten authz dependency must be
    # a boot failure everywhere, not just in production (live-verified: adding
    # a throwaway unmarked route makes the app refuse to start with a
    # RuntimeError naming it; removing it restores clean boot).
    assert_all_routes_protected(app)
    logger.info(
        "D-05 route-coverage boot scan: all routes are require_permission-protected, "
        "public()/allowlist-marked, or in-body-protected (signals) — no offenders"
    )

    # Start background health probe (re-probes every 30s)
    bg_task = asyncio.create_task(_refresh_health(app))
    # RunSweeper — expires runs stuck in "running" with no activity (conversational
    # runs have no Temporal owner, so nothing else ever terminates an abandoned one).
    from workers.run_sweeper import RunSweeper
    run_sweeper_task = asyncio.create_task(RunSweeper().run())
    logger.info("RunSweeper started")
    # Start artifact event listener (routes pipeline stage transitions via Redis pub/sub)
    artifact_listener_task = asyncio.create_task(_artifact_event_listener())
    # Start connector health probe (re-probes connector health every 30s)
    connector_health_task = asyncio.create_task(_refresh_connector_health(app))

    # Webhook consumer tasks — one per connector kind, spawned only when
    # ENABLE_WEBHOOK_TRIGGERS=true AND the Temporal client is available.
    # The M3 worker-pool gate (ENABLE_WORKER_POOL) is independent of this.
    webhook_consumer_tasks = []
    if ENABLE_WEBHOOK_TRIGGERS and app.state.temporal_client is not None:
        from webhooks.consumer import WebhookConsumer
        # github_actions is DELIBERATELY ABSENT from this list. WebhookConsumer starts
        # an SDLCWorkflow per event, which for a CI stream would launch a full pipeline
        # several times per push. It gets PipelineRunConsumer below instead.
        for _ck in ["github", "jira", "azure_repos"]:
            _consumer = WebhookConsumer(_ck, app.state.temporal_client)
            webhook_consumer_tasks.append(asyncio.create_task(_consumer.run()))
        logger.info(
            "Webhook consumers started for connectors: github, jira, azure_repos"
        )

        # CI pipeline runs: records state, never starts a workflow.
        from webhooks.pipeline_consumer import PipelineRunConsumer
        webhook_consumer_tasks.append(asyncio.create_task(PipelineRunConsumer().run()))
        logger.info("Pipeline-run consumer started for connector: github_actions")

    # Durable async LangGraph checkpointer pool (enterprise) — opened once here so the
    # agent graphs' astream/ainvoke can persist execution state. No-op in local mode
    # (MemorySaver, no pool). Must run inside the event loop, hence here not at import.
    from config.checkpoint import aopen_checkpointer
    await aopen_checkpointer()

    # Langfuse tracing singleton — construct here so the first traced request does not
    # pay init latency and a misconfig is logged once at startup (no-op when disabled).
    from shared.observability import get_langfuse_client  # noqa: PLC0415
    get_langfuse_client()

    yield  # Application running

    # Shutdown
    from config.checkpoint import aclose_checkpointer
    await aclose_checkpointer()

    # Flush any buffered Langfuse spans before the process exits.
    from shared.observability import flush_langfuse  # noqa: PLC0415
    flush_langfuse()
    for _wt in webhook_consumer_tasks:
        _wt.cancel()
        try:
            await _wt
        except asyncio.CancelledError:
            pass
    connector_health_task.cancel()
    try:
        await connector_health_task
    except asyncio.CancelledError:
        pass
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass
    run_sweeper_task.cancel()
    try:
        await run_sweeper_task
    except asyncio.CancelledError:
        pass
    artifact_listener_task.cancel()
    try:
        await artifact_listener_task
    except asyncio.CancelledError:
        pass
    if audit_retry_task is not None:
        audit_retry_task.cancel()
        try:
            await audit_retry_task
        except asyncio.CancelledError:
            pass
    if app.state.blob_client is not None:
        await app.state.blob_client.close()
    if ENABLE_WORKER_POOL and hasattr(app.state, "redis_pool"):
        await app.state.redis_pool.disconnect()


# Initialize FastAPI application with metadata
app = FastAPI(
    title="AI Assistant API",
    description="Main API with chat and development endpoints",
    version="1.0.0",
    lifespan=lifespan,
)

# Prometheus /metrics (REQ-M3-04). Exposed before routers so HTTP instrumentation
# wraps every subsequent route. /metrics is added to _EXEMPT_PATHS below because
# the Prometheus scraper sends no JWT.
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(BASE_DIR, "files")
app.mount("/generated", StaticFiles(directory=FILES_DIR), name="generated")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_origin_regex=cors_allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Paths that bypass JWT validation.
# /auth/ws-ticket is NOT exempt — only authenticated users may mint tickets.
# /metrics is exempt because the Prometheus scraper sends no JWT; labels carry
# only agent_type/connector enums, no credentials or user data (T-M3-05).
_EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/metrics", "/auth/login"}


@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    path = request.url.path
    if (
        path in _EXEMPT_PATHS
        or path.startswith("/generated")
        or path.startswith("/static")
        # Webhook providers cannot send JWT tokens — signature verification IS the
        # authentication for /webhooks/* paths (T-m6-07-S). This is a prefix check,
        # NOT an entry in _EXEMPT_PATHS, to make the intent explicit.
        or path.startswith("/webhooks/")
        # SCIM uses per-tenant bearer credentials, not the platform JWT (Pitfall 1,
        # T-7.4-SC). /scim/ is already in _PUBLIC_PREFIXES in dependency.py (D-05
        # boot scan passes). JWT middleware exemption is needed separately because
        # the middleware runs before the route handler and would reject the request
        # before the SCIM endpoint can verify its own credential.
        or path.startswith("/scim/")
    ):
        return await call_next(request)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    token = auth_header[7:]
    # Provider label bounded to OIDC_PROVIDERS keys for low-cardinality metrics (T-7.3-03).
    # Config-driven via OIDC_PROVIDER (validated against OIDC_PROVIDERS at import in
    # resolve_provider_key) — NOT hardcoded, so a multi-IdP deployment selects + meters
    # the right provider (WR-03). "local" is the HS256 path label (never reaches
    # OIDC_AUTH_ATTEMPTS; only enterprise outcomes are metered below).
    _oidc_provider = resolve_provider_key(OIDC_PROVIDER)
    # Single source of truth for the enterprise-OIDC gate — MUST match decode_token's
    # gate so an HS256 token can never be routed through the OIDC branch (CR-01).
    _is_enterprise_oidc = _is_enterprise_oidc_gate()
    try:
        claims = _decode_token(token)
        request.state.user_id = claims.get("sub", "")

        # JTI denylist check (REQ-M7-17, D-01, Wave A).
        # Guard: `if jti` ensures legacy tokens minted before this deploy (no jti claim)
        # are NOT rejected — they simply skip the denylist check (Pitfall 2, T-7.4-04).
        # Guard: `getattr(..., None)` makes the check no-op when Redis is unavailable in
        # local dev (T-7.4-02: fail-open is the accepted risk for local/dev; enterprise
        # mode requires Redis via ENABLE_SCIM startup precondition enforced in Plan 02).
        jti = claims.get("jti", "")
        if jti and getattr(request.app.state, "redis_denylist", None):
            if await is_jti_denied(
                request.app.state.redis_denylist,
                claims.get("tenant_id", ""),
                claims.get("sub", ""),
                jti,
            ):
                return JSONResponse({"detail": "Token revoked"}, status_code=401)

        if _is_enterprise_oidc:
            # ENTERPRISE OIDC PATH (D-01): tenant_id is nested under
            # https://sdlc/tenant in the real Auth0 token — NEVER claims.get("tenant_id")
            # here (the flat key doesn't exist; defaulting it would bypass SC#2, Pitfall 2).
            # extract_tenant_id is the REQ-M7-14 seam: nested→flat.
            tenant_id = extract_tenant_id(claims, _oidc_provider)
            if not tenant_id:
                OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="tenant_not_found").inc()
                return JSONResponse({"detail": "tenant_not_found"}, status_code=401)
            if not await _tenant_is_registered(tenant_id):
                # Generic 401 — do not reveal which tenants exist (T-7.3-07).
                OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="tenant_not_found").inc()
                return JSONResponse({"detail": "tenant_not_found"}, status_code=401)
            request.state.tenant_id = tenant_id
            # D-02: Auth0 asserts identity+tenant only; permissions are resolved from our DB
            # under RLS (resolve_permissions_for_user) — no permissions claim trusted from Auth0.
            request.state.permissions = await resolve_permissions_for_user(
                request.state.user_id, tenant_id
            )
            OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="success").inc()
        else:
            # LOCAL / HS256 PATH (D-01): the baked JWT carries flat tenant_id and
            # permissions claims — read them directly (REQ-M7-12 / existing contract).
            # This path is unchanged; existing tokens stay valid (REQ-M7-15 / SC#4).
            request.state.tenant_id = claims.get("tenant_id", "")
            # REQ-M7-12: permissions claim propagated from JWT into request.state so
            # require_permission dependency (and signals.py's in-body phase check) can
            # read it without a per-request DB hit (D-02). D-01: this is now the ONLY
            # authz vocabulary the platform speaks — the legacy M5 "roles" claim is
            # retired (no consumer reads request.state.roles anymore; _low_cardinality_role
            # in dependency.py derives an advisory metric label directly from permissions,
            # so no separate role claim needs to be baked into the JWT or surfaced here).
            request.state.permissions = claims.get("permissions", [])

    # JWKS endpoint unreachable / transport error — an availability failure, not a
    # malformed-token failure. Map to 503, not 500, and meter as invalid_token so a
    # down IdP does not take the whole auth path down with an unhandled 500 (CR-02).
    except _JWKSUnavailableError as exc:
        if _is_enterprise_oidc:
            OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="invalid_token").inc()
        logger.warning("JWKS unavailable during token validation: %s", exc)
        return JSONResponse(
            {"detail": "Authentication temporarily unavailable"}, status_code=503
        )
    # DB outage during permission resolution — fail closed to 503 rather than
    # proceeding with an empty permission set that would strip a valid user's
    # authorization silently (WR-05).
    except PermissionResolutionError as exc:
        if _is_enterprise_oidc:
            OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="invalid_token").inc()
        logger.warning("Permission resolution unavailable during auth: %s", exc)
        return JSONResponse(
            {"detail": "Authorization temporarily unavailable"}, status_code=503
        )
    # Subclass order matters: specific exceptions BEFORE the base InvalidTokenError.
    except _pyjwt.ExpiredSignatureError:
        if _is_enterprise_oidc:
            OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="invalid_token").inc()
        return JSONResponse({"detail": "Token expired"}, status_code=401)
    except _pyjwt.InvalidAudienceError:
        # Rejects a Microsoft Graph token or any RS256 token with a wrong aud (SC#3, T-7.3-05).
        if _is_enterprise_oidc:
            OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="bad_audience").inc()
        return JSONResponse({"detail": "Invalid token"}, status_code=401)
    except _pyjwt.InvalidTokenError as exc:
        if _is_enterprise_oidc:
            OIDC_AUTH_ATTEMPTS.labels(provider=_oidc_provider, outcome="invalid_token").inc()
        return JSONResponse({"detail": f"Invalid token: {exc}"}, status_code=401)
    return await call_next(request)


def _is_allowed_browser_origin(origin: str) -> bool:
    return (
        origin.startswith(("http://localhost:", "http://127.0.0.1:"))
        or origin.endswith(".ngrok-free.app")
    )


@app.middleware("http")
async def add_browser_cors_headers(request, call_next):
    response = await call_next(request)
    origin = request.headers.get("origin", "")
    if _is_allowed_browser_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response

# ── D-05 route classification ─────────────────────────────────────────────────
# Every router registered below carries an EXPLICIT, CONSCIOUS authz dependency
# (or is covered by the assert_all_routes_protected allowlist) so the D-05 boot
# scan can prove no route ships unclassified (fail-open structurally impossible,
# T-7.2-15). Router-level `dependencies=` propagate into every route's
# `route.dependant.dependencies`, which is what the boot scan inspects via the
# __rbac_require_permission__ / __rbac_public__ sentinels (verified live).
#
# WHY "artifact:view" as the floor for agent/chat/read routes: it is the one
# permission every non-admin role is granted (product_manager, tech_lead,
# qa_lead, sre_lead, developer all carry it — see _ROLE_PERMISSIONS), so it acts
# as "any authenticated platform user" without inventing a new vocabulary entry;
# admin:* passes it via the has_permission wildcard (D-01 single model).
_VIEW_DEP = Depends(require_permission("artifact:view"))

# Include chat and development routers with versioning and tags
# Agent chat/session/download/file routes are read/interaction surfaces — gated
# on the "artifact:view" floor permission (conscious choice, not @public: these
# routes touch tenant-scoped session state and must be authenticated+authorized).
app.include_router(requirement_router_orchestrator, prefix="/sdlc/agent/requirement", tags=["requirement"], dependencies=[_VIEW_DEP])
app.include_router(requirement_router_orchestrator, prefix="/sdlc/agent/ingestion", tags=["ingestion"], dependencies=[_VIEW_DEP])
# Deployment: NEW standalone agent (readiness + connector-driven package + gated PR).
# The legacy evaluator stays at /sdlc/agent/deployment_orchestrator below.
from agents_orchestrator.deployment_agent.deployment_standalone_api import deployment_standalone_router
app.include_router(deployment_standalone_router, prefix="/sdlc/agent/deployment", tags=["deployment"], dependencies=[_VIEW_DEP])
app.include_router(orchestrator_router, prefix="/sdlc/agent/orchestrator", tags=["orchestrator"], dependencies=[_VIEW_DEP])
app.include_router(copilot_router, prefix="/sdlc/agent/copilot", tags=["copilot"], dependencies=[_VIEW_DEP])

app.include_router(requirement_router_orchestrator, prefix="/sdlc/agent/requirement_orchestrator", tags=["requirement-orchestrator"], dependencies=[_VIEW_DEP])
app.include_router(requirement_router_orchestrator, prefix="/sdlc/agent/ingestion_orchestrator", tags=["ingestion-orchestrator"], dependencies=[_VIEW_DEP])
app.include_router(design_router_orchestrator, prefix="/sdlc/agent/design", tags=["design"], dependencies=[_VIEW_DEP])
app.include_router(design_router_orchestrator, prefix="/sdlc/agent/design_orchestrator", tags=["design-orchestrator"], dependencies=[_VIEW_DEP])
# Development: active orchestrated router serves both the primary and legacy prefixes
app.include_router(development_router_orchestrator, prefix="/sdlc/agent/development", tags=["development"], dependencies=[_VIEW_DEP])
app.include_router(development_router_orchestrator, prefix="/sdlc/agent/development_orchestrator", tags=["development-orchestrator"], dependencies=[_VIEW_DEP])
# Code Review: standalone interactive review chat (read-only on the repo)
app.include_router(code_review_router, prefix="/sdlc/agent/code-review", tags=["code-review"], dependencies=[_VIEW_DEP])
# Security: standalone interactive scan chat (read-only on the repo)
app.include_router(security_router, prefix="/sdlc/agent/security", tags=["security"], dependencies=[_VIEW_DEP])
# Documentation: standalone interactive doc-generation chat (read-only on the repo; optional gated docs PR)
from agents_orchestrator.documentation_agent.documentation_standalone_api import documentation_standalone_router
app.include_router(documentation_standalone_router, prefix="/sdlc/agent/documentation", tags=["documentation"], dependencies=[_VIEW_DEP])
app.include_router(deployment_router_orchestrator, prefix="/sdlc/agent/deployment_orchestrator", tags=["deployment-orchestrator"], dependencies=[_VIEW_DEP])
# Testing: active orchestrated router serves both the primary and legacy prefixes (mirrors dev agent pattern)
app.include_router(testing_router_orchestrator, prefix="/sdlc/agent/testing", tags=["testing"], dependencies=[_VIEW_DEP])
app.include_router(testing_router_orchestrator, prefix="/sdlc/agent/testing_orchestrator", tags=["testing-orchestrator"], dependencies=[_VIEW_DEP])
# Connector hub — GET /connectors/health (JWT-protected; NOT in _EXEMPT_PATHS).
# Health visibility is a read concern — "artifact:view" floor (conscious choice).
app.include_router(connectors_router, tags=["connectors"], dependencies=[_VIEW_DEP])
# Webhook inbound edge — JWT-exempt prefix (/webhooks/); signature IS the auth
# (T-m6-07-S). No RBAC dependency: the _PUBLIC_PREFIXES allowlist in
# shared.authz.dependency mirrors this EXACT prefix exemption so the D-05 scan
# treats it as a conscious public classification, not a silent gap.
from webhooks.router import webhooks_router
app.include_router(webhooks_router, tags=["webhooks"])
# Resource routers — JWT-protected (NOT in _EXEMPT_PATHS); tenant-scoped (T-M4-01, T-M4-02).
# projects: "artifact:view" floor for reads; Phase 6 adds require_permission("workspace:manage")
# per-route on mutations (create/archive/restore/patch) per the RBAC matrix — only
# admin/delivery_lead may mutate projects. The _VIEW_DEP floor remains so the D-05 scan
# treats the router as protected even though mutation routes carry their own tighter dep.
app.include_router(workspaces_router, prefix="/workspaces", tags=["workspaces"], dependencies=[_VIEW_DEP])
app.include_router(projects_router, prefix="/projects", tags=["projects"], dependencies=[_VIEW_DEP])
# runs_router: POST /runs is THE fixed-permission route named by the plan
# (run:create) — given its OWN per-route dependency below, NOT a router-level
# floor, because the other /runs/* routes (list/detail/steps/approvals) are
# read/record-decision surfaces appropriately gated on the view floor instead.
app.include_router(runs_router, prefix="/runs", tags=["runs"], dependencies=[_VIEW_DEP])
app.include_router(audit_router, prefix="/audit", tags=["audit"], dependencies=[_VIEW_DEP])
# GET /cost — per-tenant spend aggregate + budget-utilization signal (REQ-M9-07,
# REQ-M9-09). Carries its OWN require_permission("cost:view") dependency (Phase 6:
# tightened from artifact:view per RBAC matrix — admin/delivery_lead/security_auditor
# only) PLUS the _VIEW_DEP floor at include — _VIEW_DEP is the weaker permission
# so cost:view is the effective gate; D-05 boot scan stays green (the router dep
# carries __rbac_require_permission__ either way).
app.include_router(cost_router, prefix="/cost", tags=["cost"], dependencies=[_VIEW_DEP])
# GET /traces, /traces/metrics, /traces/{id} — read-only live proxy to the self-hosted
# Langfuse Public API, mapped to the TraceListItem/Trace/TraceMetrics contract the
# frontend already expects. Carries its OWN require_permission("trace:view") dependency
# plus the _VIEW_DEP floor (trace:view is the effective gate). Tenant-scoped server-side.
app.include_router(traces_router, prefix="/traces", tags=["traces"], dependencies=[_VIEW_DEP])
# Run-scoped audit + evidence routes — mounted WITHOUT a prefix so public paths are
# /runs/{run_id}/audit and /runs/{run_id}/evidence (REQ-M8-06, REQ-M8-07).
# These routes carry their OWN run_param-aware require_permission("artifact:view")
# dependency — no additional blanket _VIEW_DEP to avoid double-apply (D-05 conscious
# choice: per-route dep is the correct pattern here, not router-level floor).
app.include_router(audit_runs_router, tags=["audit"])
# eval_runs_router: mounted WITHOUT prefix so public path is /runs/{run_id}/eval.
# Per-route require_permission("artifact:view", run_param="run_id") — same gate as
# audit_runs_router.  No _VIEW_DEP floor to avoid double-apply (milestone-8-04 decision).
app.include_router(eval_runs_router, tags=["eval"])
app.include_router(evidence_router, tags=["evidence"])
# Artifacts, connectors (resource), signals — absolute in-router paths; no prefix (T-M4-04, T-M4-05, T-M4-06).
app.include_router(artifacts_router, tags=["artifacts"], dependencies=[_VIEW_DEP])
# connectors_resource_router: GET routes (list/detail) use the view floor;
# install/disconnect (connector:manage) get their OWN per-route dependency below
# — seeded now so the boot scan passes and the matrix is enforced; live
# connect/disconnect provider plumbing is 7.6 (plan note, D-07 scope).
app.include_router(connectors_resource_router, tags=["connectors"], dependencies=[_VIEW_DEP])
# signals_router: send_signal performs its OWN in-body phase-derived permission
# check (_check_permission_for_phase, REQ-M7-11/Pattern 4) — NOT a router-level
# dependency (the required permission cannot be a static factory parameter).
# Covered by _SIGNALS_IN_BODY_PROTECTED_PATHS in the boot-scan allowlist.
app.include_router(signals_router, tags=["signals"])
# admin_router: self-protected via its own router-level _require_admin dependency
# (admin:* gate, tenant-wide). Mounted under /admin — self-serve RBAC role assignment
# (the D-09 deferred feature). Cross-tenant blocked by FORCE RLS on user_workspace_roles.
app.include_router(admin_router, prefix="/admin", tags=["admin"])
# custom_roles_router: self-gated via router-level require_permission("role:manage").
# Carries its own /admin/custom-roles prefix; tenant isolation by FORCE RLS.
app.include_router(custom_roles_router, tags=["admin"])
# Local email+password auth (Phase 3): POST /auth/login (JWT-exempt, in _EXEMPT_PATHS)
# + GET /auth/me (JWT-validated). Both public()-marked for the D-05 boot scan.
app.include_router(auth_local_router, tags=["auth"])
# Platform-tier cross-tenant surface (Phase 3): GET /platform/organizations.
# platform:*-gated via require_platform_admin (tenant-less callers, D-05 sentinel-marked).
app.include_router(platform_router, tags=["platform"])
# Model Provider (BYOK) config + options surface (P2). Both routers carry their
# OWN router-level require_permission gates baked into the APIRouter definition —
# model_router on "model:manage", model_options_router on "run:create" — so NO
# _VIEW_DEP floor here (the per-router dep is the effective gate and carries
# __rbac_require_permission__ for the D-05 boot scan).
app.include_router(model_router, tags=["model"])
app.include_router(model_options_router, tags=["model"])
# Capabilities API (D7): read-only per-agent capability view for the UI panel.
# Native tools are informational only; curated shown with default-on flag.
# Router carries its own require_permission("artifact:view") gate — no _VIEW_DEP floor.
app.include_router(capabilities_router, tags=["capabilities"])
# Agent Profile lifecycle (Phase 2 Prompt Management): versioned draft/publish/rollback.
# Router carries its OWN router-level require_permission("artifact:view") floor plus
# per-route tighter gates (project:update on draft/preview, workspace:manage on
# publish/unpublish) — every route holds a require_permission sentinel so the D-05
# boot scan stays green; no _VIEW_DEP floor at include (mirrors capabilities_router).
from shared.routers.agent_profiles import agent_profiles_router
app.include_router(agent_profiles_router, tags=["agent-profiles"])
# Vendor+custom skill catalog / authoring / toggles (Phase 4). Same layering as
# agent_profiles: router-level artifact:view floor + per-route project:update /
# workspace:manage sentinels so the D-05 boot scan stays green.
from shared.routers.agent_skills import agent_skills_router
app.include_router(agent_skills_router, tags=["agent-skills"])
# Per-user/per-agent chat session history (§11A). Creator-scoped in the router +
# tenant FORCE-RLS in the service; _VIEW_DEP is the floor.
app.include_router(conversations_router, tags=["conversations"], dependencies=[_VIEW_DEP])
# Dev-workspace cascade (B1): ADO project/repo/branch picker endpoints called by the
# Development agent modal before launching a session.  Gated by the artifact:view floor
# (any authenticated platform user).  {project_id} path param reserved for future
# per-project connector resolution — creds come from env for now.
from shared.routers.dev_workspace import dev_workspace_router
app.include_router(dev_workspace_router, prefix="/dev", tags=["dev-workspace"], dependencies=[_VIEW_DEP])
from shared.routers.code_review_workspace import code_review_workspace_router
app.include_router(code_review_workspace_router, prefix="/code-review", tags=["code-review-workspace"], dependencies=[_VIEW_DEP])
from shared.routers.security_workspace import security_workspace_router
app.include_router(security_workspace_router, prefix="/security", tags=["security-workspace"], dependencies=[_VIEW_DEP])
from shared.routers.deployment_workspace import deployment_workspace_router
app.include_router(deployment_workspace_router, prefix="/deployment", tags=["deployment-workspace"], dependencies=[_VIEW_DEP])
from shared.routers.documentation_workspace import documentation_workspace_router
app.include_router(documentation_workspace_router, prefix="/documentation", tags=["documentation-workspace"], dependencies=[_VIEW_DEP])
# MCP server registry (P-MCP). Per-route connector:view / connector:manage gates baked
# into the router, so NO _VIEW_DEP floor here. Mounted only when MCP_ENABLED so the
# feature is dark-launchable; absent flag → no MCP surface at all.
from config.env import MCP_ENABLED as _MCP_ENABLED
if _MCP_ENABLED:
    from shared.routers.mcp_registry import mcp_registry_router
    app.include_router(mcp_registry_router, tags=["mcp"])
# SCIM 2.0 provisioning edge (Wave A, REQ-M7-16/17).
# /scim/ is JWT-exempt (middleware + _PUBLIC_PREFIXES) and uses per-tenant bearer
# credentials instead (verify_scim_credential, T-7.4-05, D-02).
# The ENABLE_SCIM startup guard (above) ensures Redis is available for JTI denylist
# in enterprise mode before any SCIM request can be served (T-7.4-10).
from scim.router import scim_router
app.include_router(scim_router, tags=["scim"])



# Root endpoint
@app.get("/")
async def root():
    return {"message": "AI Assistant API is running"}


# Health check endpoint — reads from app.state.health_cache (non-blocking)
@app.get("/health")
async def health_check(request: Request):
    cache = getattr(request.app.state, "health_cache", {})
    infra_values = {k: v for k, v in cache.items() if k != "probed_at"}
    # In local mode infra errors (postgres/redis unreachable) are expected — only
    # enterprise mode treats them as failures. "initializing" still blocks "ok".
    _LOCAL_OK = {"ok", "not configured", "disabled", "removed"}
    if AGENT_RUNTIME_MODE == "local":
        all_ok = all(
            v in _LOCAL_OK or v.startswith("error")
            for v in infra_values.values()
        )
    else:
        all_ok = all(v in ("ok", "not configured") for v in infra_values.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "runtime_mode": AGENT_RUNTIME_MODE,
        "checkpointers": get_checkpointer_status(),
        **cache,
    }


@app.post("/auth/ws-ticket", dependencies=[Depends(require_permission("artifact:view"))])
async def mint_ticket_endpoint(request: Request):
    """Mint a single-use WebSocket ticket valid for 20 s.

    Requires a valid JWT (not in _EXEMPT_PATHS — middleware validates it first)
    AND the "artifact:view" floor permission (D-05 conscious classification —
    minting a ticket is an authenticated-platform-user action, not a public one;
    every non-admin role carries this permission, so this is "any real user").
    Client connects via wss://host/ws/agent/...?ticket=<returned ticket>.
    """
    user_id = getattr(request.state, "user_id", "")
    tenant_id = getattr(request.state, "tenant_id", "")
    try:
        ticket = await _mint_ws_ticket(user_id, tenant_id)
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail=f"Redis unavailable — cannot mint WS ticket: {exc}",
        )
    return {"ticket": ticket, "ttl_seconds": 20}


@app.get("/auth/permissions", dependencies=[Depends(public())])
async def get_my_permissions(request: Request):
    """Return the authenticated caller's effective DB-resolved permissions.

    REQ-M7-12 frontend half (milestone-7.2 plan 05): the BFF calls this once per
    session build to enrich Session.permissions with the canonical resolver's
    output — the SAME resolver that bakes the JWT `permissions` claim at login
    (D-02, single source of truth, no TypeScript re-implementation of the
    role->permission join).

    BOOTSTRAP DEADLOCK — why public(), not require_permission(...):
    this endpoint is what the frontend calls to OBTAIN its permissions, so at
    call time the caller's `permissions` claim is still `[]` (a fresh BFF token
    minted from the base, pre-enrichment session). Gating it behind
    require_permission would read request.state.permissions == [], 403 the
    bootstrap call, and leave the user permanently empty-handed — a deadlock.
    public() only opts out of the per-route PERMISSION check; the
    jwt_auth_middleware still runs (this path is not in _EXEMPT_PATHS) and
    populates request.state.{user_id,tenant_id} from the signature-verified
    JWT, so authentication — and therefore identity — is fully established
    (T-7.2-22).

    Identity is read EXCLUSIVELY from request.state (never query/body params),
    and resolve_permissions_for_user runs under tenant RLS (D-03), so a caller
    can only ever read their OWN tenant-scoped permissions — no enumeration,
    no cross-tenant leak. Missing identity fails closed to {"permissions": []},
    mirroring the resolver's own fail-closed contract.
    """
    user_id = getattr(request.state, "user_id", "") or ""
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not user_id or not tenant_id:
        return {"permissions": []}
    try:
        permissions = await resolve_permissions_for_user(user_id, tenant_id)
    except PermissionResolutionError:
        # A DB outage must not silently return {"permissions": []} — that would let
        # the frontend treat an admin as zero-permission. Surface 503 instead (WR-05).
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Permission resolution temporarily unavailable",
        )
    return {"permissions": permissions}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("process_api:app", host="0.0.0.0", port=80, log_level="info")
