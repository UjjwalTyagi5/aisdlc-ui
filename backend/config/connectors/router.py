"""Connector hub HTTP router (milestone-3 / milestone-6).

Exposes GET /connectors/health, which reports the per-connector health status.

The background probe in process_api._refresh_connector_health populates
app.state.connector_health_cache every 30 seconds for all five connectors.
If the cache has not been populated yet (e.g. the app is freshly started or
the lifespan probe is still running), this handler performs an inline probe so
the first caller never receives an empty response (REQ-M6-11).

SECURITY: this route is deliberately NOT in _EXEMPT_PATHS — the JWT middleware
must enforce auth (401 without a valid Bearer token). The health payload may
reveal connector configuration/state, so it is treated as an authenticated
resource (T-M3-05).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from config.connector_factory import list_available_connectors

connectors_router = APIRouter()

# Connector names present in a fully-probed cache (every registered connector).
#
# CAREFUL: a name listed here whose health_check() RAISES is dropped from the cache by
# _probe_all_connectors, so the subset test below never passes and this endpoint
# re-probes inline on every single request. Every connector's health_check must catch
# Exception and return a ConnectorHealth instead of raising.
_EXPECTED_CONNECTOR_NAMES = frozenset(
    {
        "azure_devops",
        "jira",
        "github_issues",
        "azure_repos",
        "azure_pipelines",
        "slack",
        "github_actions",
        "ms_teams",
        "sharepoint",
        "figma",
        "confluence",
        "sonarqube",
    }
)


@connectors_router.get("/connectors/health")
async def connectors_health(request: Request):
    """Return the per-connector health snapshot.

    Normally reads app.state.connector_health_cache, which is populated at
    lifespan startup and refreshed every 30 seconds by the background probe
    (_refresh_connector_health in process_api.py).

    If the cache is absent or does not yet contain all five connectors (startup
    race condition or test environment without lifespan), performs a one-time
    inline probe so callers always receive complete health data.  The inline
    probe uses the same asyncio.gather(return_exceptions=True) pattern as the
    background probe (REQ-M6-11, T-m6-08-D).
    """
    cache = getattr(request.app.state, "connector_health_cache", None)

    # Inline probe when cache is absent or missing the expected connector entries.
    # This covers: (1) first request before background probe completes, (2) tests
    # that instantiate TestClient without running the lifespan context manager.
    cached_connectors = set(cache.keys()) - {"probed_at"} if cache else set()
    if not cache or not _EXPECTED_CONNECTOR_NAMES.issubset(cached_connectors):
        from process_api import _probe_all_connectors
        cache = await _probe_all_connectors()
        # Best-effort write-back so background loop sees a warm cache on next cycle.
        try:
            request.app.state.connector_health_cache = cache
        except Exception:
            pass

    return {
        "connectors": {k: v for k, v in cache.items() if k != "probed_at"},
        "probed_at": cache.get("probed_at", 0),
    }
