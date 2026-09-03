"""Figma connector — read design files and export frame images over the REST API.

Serves the Integrations catalogue's "Design & prototyping" category: ground the
Design & Architecture agent in the design that actually exists, instead of asking it
to imagine one from prose.

TWO AUTH SHAPES, ONE LADDER. Figma accepts either a Personal Access Token or an
OAuth2 bearer token, and they use DIFFERENT HEADERS — `X-Figma-Token` for a PAT,
`Authorization: Bearer` for OAuth. Sending the wrong one is a 403 with no useful
body, so `_figma_headers` picks by which credential resolved rather than by a flag
the caller passes. A tenant that has both keeps the OAuth token: it is user-scoped
and revocable from Figma's side, which a PAT is not.

Credentials resolve through the standard ladder (mirrors GitHubActionsConnector):
    secret_store(tenant, "figma-access-token" | "figma-pat")  ← Integrations form / OAuth callback
      → Key Vault "{tenant}-figma-pat"                        ← both rungs tenant-scoped
A DISCONNECTED_MARKER on `figma-connected` short-circuits the whole ladder with NO
fallback, so a disconnect is honoured rather than silently reverting to a global
credential. The marker is checked FIRST because this kind has two credential refs and
tombstoning only one of them would leave the other live.

READ-ONLY BY DESIGN. capability_manifest declares no write capabilities and
write_adapter raises. The Figma REST API's only meaningful write is POSTing a comment,
which no agent here has a reason to do — a design tool is an input to this platform,
not an output of it. `listen_capabilities` is likewise empty: Figma webhooks v2 exist
but nothing would consume the events today (the webhook consumers went away with
Temporal), and a subscription nobody reads is worse than none.

NOTE (A9 — ASSUMED): Figma's documented rate limit is per-token with a `Retry-After`
on 429, but the exact cost weighting per endpoint is not verified against a live 429
in this environment. Honoured when present; exponential backoff otherwise.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

import shared.keyvault as _keyvault
from config.connectors.base import BaseConnector
from config.connectors.http_client import get_async_client
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)
from config.connectors.rate_limit import (
    _TenantRateLimitState,
    await_backoff,
    record_rate_limit_hit,
)
from shared.services.metrics import CONNECTOR_RATE_LIMIT_BACKOFFS

logger = logging.getLogger(__name__)

_FIGMA_API_BASE = "https://api.figma.com"

# The rate-limit bucket used when a caller does not name a tenant. It is a bucket key,
# NOT a tenant id — credential resolution must never treat it as one.
_DEFAULT_BUCKET = "default"
# Platform convention: the global health probe passes this instead of a real tenant.
_HEALTH_PROBE_TENANT = "__health_probe__"

# A Figma file key is the path segment after /file/, /design/ or /proto/ in a share
# URL. Users paste the URL far more often than the key, so every entry point accepts
# either — see extract_file_key.
_FILE_KEY_RE = re.compile(r"figma\.com/(?:file|design|proto)/([A-Za-z0-9]+)")
# A bare key, when someone did paste just the key.
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9]{10,}$")

# Frames come back as URLs to rendered assets, not bytes. Figma expires those URLs,
# and this is the documented window — surfaced to callers so a stale link is
# diagnosable rather than mysterious.
IMAGE_URL_TTL_SECONDS = 30 * 24 * 60 * 60


# Resolved credentials, cached per platform tenant: tenant_id -> (auth, expires_at).
#
# WHY: every REST call resolves credentials, and a cold resolution is up to eight
# secret-store/Key-Vault round trips. Without this, listing frames and exporting three
# images cost more time in the vault than at Figma.
#
# The TTL is short and writes invalidate explicitly (`clear_auth_cache`, called from
# the credentials and disconnect endpoints), so the window where this can serve a stale
# answer is bounded by a path nobody drives by hand. Module-level and never persisted,
# mirroring msgraph's `_token_cache`.
_AUTH_CACHE_TTL_S = 60
_auth_cache: Dict[str, Tuple[dict, float]] = {}


def clear_auth_cache(tenant_id: str = "") -> None:
    """Drop cached credentials for one tenant, or all when none is named.

    MUST be called whenever a tenant's Figma credential changes — connect, reconnect
    or disconnect — or the old credential stays live for up to the TTL.
    """
    if tenant_id:
        _auth_cache.pop(tenant_id, None)
    else:
        _auth_cache.clear()


class ConnectorCredentialsMissing(RuntimeError):
    """Raised when a Figma call is attempted for a tenant with no credential.

    Distinct from an HTTP 403: nothing was sent, so there is nothing to diagnose on
    Figma's side — the fix is always to connect the integration.
    """


def extract_file_key(url_or_key: str) -> str:
    """Return the Figma file key from a share URL, or the key unchanged.

    Accepts https://www.figma.com/design/AbC123/My-File?node-id=1-2 and a bare key.

    A BARE KEY NEEDS 10+ ALPHANUMERICS (`_BARE_KEY_RE`). The example here used to be
    "AbC123", which is six and does not match its own regex — real Figma keys are
    around 22 characters, and the floor exists so an ordinary word is not mistaken for
    a key. Returns "" when neither shape matches, so callers can give a specific error
    instead of sending a doomed request.
    """
    value = (url_or_key or "").strip()
    if not value:
        return ""
    match = _FILE_KEY_RE.search(value)
    if match:
        return match.group(1)
    if _BARE_KEY_RE.match(value):
        return value
    return ""


class FigmaConnector(BaseConnector):
    """Figma connector — file/node reads and frame image exports over REST.

    Per-tenant rate-limit state is class-level so one tenant's 429 backoff never
    blocks another (REQ-M6-12, REQ-M3-11).
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        """Constructor stores only non-secret config — never a token (REQ-M3-10).

        Args:
            org_url:   Optional default file URL or key. The configured
                       `figma-file-key` ref takes precedence; this is a fallback.
            tenant_id: Run context, NOT a credential. Set by the connector factory so
                       auth_adapter() resolves the tenant-scoped token without every
                       call site threading it through (REQ-M7-01).
        """
        self._org_url = (org_url or "").strip()
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "figma"

    @property
    def display_name(self) -> str:
        return "Figma"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def _resolve_ref(
        self, tenant_id: str, ref: str
    ) -> Tuple[Optional[str], bool]:
        """Resolve one credential ref through the standard ladder.

        Returns (value, disconnected). When `disconnected` is True the tenant has
        explicitly disconnected this connector and NO fallback tier may be consulted —
        otherwise a disconnect would silently revert to a global credential.
        """
        value: Optional[str] = None
        if tenant_id and tenant_id != _HEALTH_PROBE_TENANT:
            try:
                from shared.services import secret_store  # lazy: avoid import cycle

                stored = await secret_store.get_secret(tenant_id, ref)
                if stored == secret_store.DISCONNECTED_MARKER:
                    return None, True
                value = stored
            except Exception:  # noqa: BLE001 — degrade to the tenant KV tier
                value = None
        # The vault tiers are guarded too: these run under asyncio.gather, where one
        # coroutine raising leaves its sibling's result unretrieved. A vault hiccup
        # should fall through to the next tier, not abort the whole resolution.
        if not value and tenant_id:
            try:
                value = await _keyvault.load_secret(ref, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001
                value = None
        return value, False

    @staticmethod
    async def _config_ref(tenant_id: str, ref: str) -> str:
        """Read a NON-SECRET routing value from the tenant secret store only.

        `figma-file-key` is configuration, not a credential — it is exactly what
        `shared.services.notification_targets.figma_target` reads, and that reads the
        secret store alone. Walking the Key Vault ladder for it was two wasted round
        trips per call and would have let a global vault entry supply a default file to
        a tenant that never chose one.
        """
        if not tenant_id or tenant_id == _HEALTH_PROBE_TENANT:
            return ""
        try:
            from shared.services import secret_store  # lazy: avoid import cycle

            value = await secret_store.get_secret(tenant_id, ref)
        except Exception:  # noqa: BLE001 — routing config must never break a call
            return ""
        if not value or value == secret_store.DISCONNECTED_MARKER:
            return ""
        return value

    @staticmethod
    async def _marker_state(tenant_id: str) -> Tuple[bool, bool]:
        """Read the `figma-connected` marker. Returns (connected, disconnected).

        Deliberately consults ONLY the secret store, unlike `_resolve_ref`. The marker
        is written exclusively by POST /connectors/figma/credentials and the OAuth
        callback, both of which write there — so the Key Vault tier can never
        hold it, and asking them cost two wasted round trips on every call for any
        tenant that had not connected.
        """
        if not tenant_id or tenant_id == _HEALTH_PROBE_TENANT:
            return False, False
        try:
            from shared.services import secret_store  # lazy: avoid import cycle

            value = await secret_store.get_secret(tenant_id, "figma-connected")
        except Exception:  # noqa: BLE001 — an unreadable marker is not a disconnect
            return False, False
        if value == secret_store.DISCONNECTED_MARKER:
            return False, True
        return bool(value), False

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve a Figma credential ephemerally.

        Returns {"token", "scheme", "file_key"} where `scheme` is "oauth" or "pat" —
        the caller needs it because the two use different headers. Both are empty when
        the tenant has disconnected or never connected; the caller turns that into a
        clear "not connected" rather than a 403 from Figma.

        Results are cached per tenant for `_AUTH_CACHE_TTL_S` (see the note on
        `_auth_cache`): every REST call resolves credentials, and a full resolution is
        up to eight vault round trips. Writes to the credential invalidate the entry,
        so the cache is never what makes a just-connected tenant look disconnected.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        The return value is never stored on self and must not be logged
        (REQ-M3-10, REQ-M6-14).
        """
        tid = tenant_id or self._tenant_id
        if not tid:
            raise ValueError(
                "tenant_id is required for FigmaConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )

        # ── A project member's own PAT, checked BEFORE the cache ──────────────
        # And deliberately never written to it: `_auth_cache` is keyed by TENANT,
        # while this credential belongs to one (project, owner). Caching it would
        # hand one person's Figma token to every other caller in the tenant until
        # the TTL expired — a cross-project credential leak, not a stale read.
        #
        # `account` carries the default file (a share URL or a bare key), the same
        # field the org-level form calls "Default file".
        override = await self._resolve_credential_override(tid, "figma")
        if override and override.token:
            return {
                "token": override.token,
                # Always "pat": a personal access token is what this form takes.
                # OAuth is a tenant-level connection nobody types by hand.
                "scheme": "pat",
                "file_key": extract_file_key(override.account or "")
                or (extract_file_key(self._org_url) if self._org_url else ""),
            }

        if not self._tenant_fallback_allowed():
        # NO TENANT FALLBACK. This credential belongs to a person
        # (base.PERSONAL_CREDENTIAL_KINDS). Without one for the acting user
        # this connector is NOT connected — borrowing a shared token would make
        # it work for a project that never configured it, and record the work
        # against whoever minted that token.
            return {
                "token": "",
                "scheme": "pat",
                "file_key": extract_file_key(self._org_url) if self._org_url else "",
            }

        cached = _auth_cache.get(tid)
        if cached is not None and cached[1] > time.time():
            auth = dict(cached[0])  # a copy — callers must not mutate the cache
        else:
            auth = await self._resolve_auth(tid)
            _auth_cache[tid] = (dict(auth), time.time() + _AUTH_CACHE_TTL_S)

        # The org_url default is applied OUTSIDE the cache because it is per-INSTANCE
        # while the cache is per-TENANT. Baking it in would let one connector's
        # constructor hint leak into another connector for the same tenant.
        if auth["token"] and not auth["file_key"] and self._org_url:
            auth["file_key"] = extract_file_key(self._org_url)
        return auth

    async def _resolve_auth(self, tid: str) -> dict[str, Any]:
        """The uncached credential resolution behind auth_adapter."""
        _EMPTY = {"token": "", "scheme": "", "file_key": ""}

        # The marker is authoritative for connectedness because this kind has TWO
        # credential refs. Checking it first is what makes a disconnect stick even
        # when only one of the two was ever written.
        _connected, disconnected = await self._marker_state(tid)
        if disconnected:
            return dict(_EMPTY)

        # The two credential refs are independent lookups and only their PRECEDENCE is
        # ordered, so they are fetched concurrently and chosen afterwards. Resolving
        # them in sequence meant a tenant on a PAT always paid the full cost of missing
        # the OAuth ref first.
        (oauth_token, oauth_disconnected), (pat, pat_disconnected) = await asyncio.gather(
            self._resolve_ref(tid, "figma-access-token"),
            self._resolve_ref(tid, "figma-pat"),
        )
        if oauth_disconnected or pat_disconnected:
            return dict(_EMPTY)

        # OAuth wins over a PAT when a tenant has both: it is user-scoped and can be
        # revoked from Figma's side, which a PAT cannot.
        token, scheme = (oauth_token, "oauth") if oauth_token else (pat, "pat")
        if not token:
            # No credential — return before resolving the default file. A file key is
            # useless without a token, and this is the hot path for any tenant that
            # never connected.
            return dict(_EMPTY)

        # Tenant-scoped values only — the per-instance org_url default is layered on in
        # auth_adapter, after the cache.
        file_key = await self._config_ref(tid, "figma-file-key")
        return {"token": token, "scheme": scheme, "file_key": file_key}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="figma",
            read_capabilities={
                "get_file": CapabilityEntry(
                    status="implemented",
                    description=(
                        "GET /v1/files/{key} — the document tree. `depth` is honoured "
                        "and defaults to 2, because a full-depth fetch of a real design "
                        "file is megabytes of JSON no prompt can hold."
                    ),
                ),
                "get_file_nodes": CapabilityEntry(
                    status="implemented",
                    description="GET /v1/files/{key}/nodes?ids= — specific nodes at full depth",
                ),
                "list_frames": CapabilityEntry(
                    status="implemented",
                    description=(
                        "Derived from get_file — flattens canvases to their top-level "
                        "FRAME/COMPONENT children so a caller can name a screen before "
                        "exporting it. Not a distinct Figma endpoint."
                    ),
                ),
                "export_images": CapabilityEntry(
                    status="implemented",
                    description=(
                        "GET /v1/images/{key}?ids= — renders nodes and returns URLs "
                        f"(png/jpg/svg/pdf). URLs expire after ~{IMAGE_URL_TTL_SECONDS // 86400} days."
                    ),
                ),
                "list_team_projects": CapabilityEntry(
                    status="not_supported",
                    description="Deferred — file discovery is out of scope; callers supply a file key",
                ),
                "get_comments": CapabilityEntry(
                    status="not_supported",
                    description="Deferred — no consumer; design feedback is not an SDLC input here",
                ),
            },
            # Deliberately empty. See the module docstring: the only Figma write is a
            # comment, and nothing on this platform has a reason to author one.
            write_capabilities={},
            # Deliberately empty. Figma webhooks v2 exist, but no consumer survives the
            # Temporal removal, and a subscription nobody drains is worse than none.
            listen_capabilities={},
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "FigmaConnector.webhook_receiver: this connector declares no listen "
            "capabilities — there is no inbound Figma webhook route to deliver here."
        )

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Wait out any active backoff window for this tenant (REQ-M6-12)."""
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the credential with GET /v1/me.

        A missing credential is `unhealthy` with a named reason rather than an
        exception: the global env probe runs with no tenant, and a raising probe is
        dropped from the health cache, which makes GET /connectors/health re-probe
        inline on every request.

        MUST NOT raise.
        """
        start = time.time()
        try:
            auth = await self.auth_adapter(self._tenant_id or _HEALTH_PROBE_TENANT)
            if not auth.get("token"):
                return ConnectorHealth(
                    connector_name="figma",
                    status="unhealthy",
                    latency_ms=(time.time() - start) * 1000,
                    error="NoCredential",
                )
            client = get_async_client(timeout=15)
            resp = await client.get(
                f"{_FIGMA_API_BASE}/v1/me",
                headers=self._figma_headers(auth["token"], auth.get("scheme", "pat")),
            )
            if resp.status_code == 200:
                return ConnectorHealth(
                    connector_name="figma",
                    status="healthy",
                    latency_ms=(time.time() - start) * 1000,
                )
            # 403 on a syntactically valid token means the scopes are wrong (an OAuth
            # app without file_read, typically) — an admin can fix that, so it is
            # degraded rather than dead.
            status = "degraded" if resp.status_code == 403 else "unhealthy"
            return ConnectorHealth(
                connector_name="figma",
                status=status,
                latency_ms=(time.time() - start) * 1000,
                error=f"http_{resp.status_code}",
            )
        except Exception as exc:  # noqa: BLE001 — a probe failure is a result, not a crash
            return ConnectorHealth(
                connector_name="figma",
                status="unhealthy",
                latency_ms=(time.time() - start) * 1000,
                error=type(exc).__name__,  # NEVER str(exc) — credential leakage risk
            )

    # ── Audit ─────────────────────────────────────────────────────────────

    async def audit_emitter(self, event: ConnectorAuditEvent) -> None:
        # Route through AuditEventService (D-02) — single write path for all audit
        # inserts; PII redaction + dead-letter retry happen inside the service.
        from shared.services.metrics import observe_connector_call
        observe_connector_call(event)
        from shared.audit.service import audit_service
        from shared.audit.models import AuditEventPayload
        await audit_service.emit(
            AuditEventPayload(
                tenant_id=str(event.tenant_id),
                run_id=event.run_id if hasattr(event, "run_id") else None,
                event_type="connector_call",
                resource_type=event.connector_name,
                resource_id=event.method,
                agent_type=event.connector_name,
                actor_id=f"system:{event.connector_name}",
                payload=event.model_dump(),
            )
        )

    # ── Read / write dispatch ─────────────────────────────────────────────

    async def read_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "get_file": self.get_file,
            "get_file_nodes": self.get_file_nodes,
            "list_frames": self.list_frames,
            "export_images": self.export_images,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        raise ValueError(
            "FigmaConnector exposes no write operations — Figma is a read-only "
            "design source on this platform (see capability_manifest)."
        )

    # ── Internal HTTP helper ──────────────────────────────────────────────

    @staticmethod
    def _figma_headers(token: str, scheme: str) -> Dict[str, str]:
        """Pick the header shape Figma expects for this credential type.

        A PAT goes in X-Figma-Token; an OAuth2 access token goes in Authorization.
        Swapping them yields a 403 with an unhelpful body, so this is decided from the
        resolved credential rather than from anything a caller passes.
        """
        if scheme == "oauth":
            return {"Authorization": f"Bearer {token}"}
        return {"X-Figma-Token": token}

    def _auth_tenant(self, tenant_id: str) -> str:
        """Map a rate-limit bucket name onto the tenant used for credential lookup.

        `_DEFAULT_BUCKET` is a bucket key, not a tenant, so it must not be passed to
        auth_adapter as one — otherwise the tenant-scoped secret tier is skipped and
        the call silently uses a global credential.
        """
        if tenant_id and tenant_id != _DEFAULT_BUCKET:
            return tenant_id
        return self._tenant_id

    async def _figma_request(
        self,
        method: str,
        path: str,
        tenant_id: str = _DEFAULT_BUCKET,
        **kwargs: Any,
    ) -> Any:
        """Execute one Figma REST call with per-tenant rate-limit backoff.

        Handles HTTP 429 by recording the hit and raising so callers can retry;
        non-429 errors raise immediately.
        """
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

        auth = await self.auth_adapter(self._auth_tenant(tenant_id))
        if not auth.get("token"):
            raise ConnectorCredentialsMissing(
                "Figma is not connected for this tenant. An admin can connect it on "
                "the Integrations page (Design & prototyping)."
            )

        client = get_async_client(timeout=60)
        resp = await client.request(
            method,
            f"{_FIGMA_API_BASE}{path}",
            headers=self._figma_headers(auth["token"], auth.get("scheme", "pat")),
            **kwargs,
        )

        if resp.status_code == 429:
            # A9 (ASSUMED): Retry-After presence on Figma 429s is documented but
            # unverified here; exponential backoff covers its absence.
            retry_after: Optional[float] = None
            header = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            if header:
                try:
                    retry_after = float(header)
                except (ValueError, TypeError):
                    pass
            record_rate_limit_hit(
                self.__class__._tenant_states, tenant_id, retry_after_seconds=retry_after
            )
            CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
                connector="figma", tenant_id=tenant_id
            ).inc()
            resp.raise_for_status()

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def _figma_request_with_retry(
        self,
        method: str,
        path: str,
        tenant_id: str = _DEFAULT_BUCKET,
        **kwargs: Any,
    ) -> Tuple[Any, int]:
        """Execute a request, retrying once on 429; return (response_data, retry_count)."""
        for attempt in range(2):
            try:
                data = await self._figma_request(method, path, tenant_id, **kwargs)
                state = self.__class__._tenant_states.get(tenant_id)
                return data, (state.retry_count if state else 0)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    continue  # await_backoff at the top of the retry waits it out
                raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def _resolve_key(self, file_key: str) -> str:
        """Normalize a caller-supplied file key or URL, raising a usable error."""
        key = extract_file_key(file_key)
        if not key:
            raise ValueError(
                f"Could not read a Figma file key from {file_key!r}. Paste the file "
                "URL (https://www.figma.com/design/<key>/<name>) or the key itself."
            )
        return key

    async def _target_key(self, file_key: str, tid: str) -> str:
        """Resolve the file to act on: the caller's, else the tenant's default.

        Shared by all three read operations. They previously each inlined this, and
        two of them fell through to `_resolve_key("")` when no default was configured
        — reporting "could not read a file key from ''" when the real answer is that
        nobody has set one.
        """
        if not file_key:
            auth = await self.auth_adapter(tid)
            file_key = auth.get("file_key", "")
            if not file_key:
                raise ValueError(
                    "No Figma file specified and no default file is configured for "
                    "this tenant. Pass a file URL or key."
                )
        return self._resolve_key(file_key)

    # ── Read operations ───────────────────────────────────────────────────

    async def get_file(
        self,
        file_key: str = "",
        depth: int = 2,
        tenant_id: str = "",
        geometry: str = "",
    ) -> Dict[str, Any]:
        """GET /v1/files/{key} — the document tree.

        `depth` defaults to 2 (canvases and their top-level frames) rather than to
        Figma's unbounded default: a real design file at full depth is megabytes of
        vector JSON, which no prompt can hold and no caller here has wanted.

        Args:
            file_key: file key or a pasted share URL. Falls back to the tenant's
                      configured `figma-file-key` when empty.
            depth:    tree depth to return; <= 0 means Figma's full depth.
            geometry: pass "paths" to include vector geometry (large — off by default).
        """
        tid = tenant_id or self._tenant_id
        key = await self._target_key(file_key, tid)

        params: Dict[str, Any] = {}
        if depth and depth > 0:
            params["depth"] = depth
        if geometry:
            params["geometry"] = geometry

        data, _ = await self._figma_request_with_retry(
            "GET", f"/v1/files/{key}", tid or _DEFAULT_BUCKET, params=params
        )
        return data if isinstance(data, dict) else {}

    async def get_file_nodes(
        self,
        file_key: str = "",
        node_ids: Optional[List[str]] = None,
        depth: int = 0,
        tenant_id: str = "",
    ) -> Dict[str, Any]:
        """GET /v1/files/{key}/nodes?ids= — specific subtrees at full depth.

        This is the endpoint to use once a frame of interest is known: it returns that
        frame in full without paying for the whole document.
        """
        tid = tenant_id or self._tenant_id
        if not node_ids:
            raise ValueError("node_ids is required — pass at least one node id.")
        key = await self._target_key(file_key, tid)

        params: Dict[str, Any] = {"ids": ",".join(node_ids)}
        if depth and depth > 0:
            params["depth"] = depth

        data, _ = await self._figma_request_with_retry(
            "GET", f"/v1/files/{key}/nodes", tid or _DEFAULT_BUCKET, params=params
        )
        return data if isinstance(data, dict) else {}

    async def list_frames(
        self, file_key: str = "", tenant_id: str = ""
    ) -> List[Dict[str, str]]:
        """Flatten a file to its top-level frames — the screens a human would name.

        NOT a Figma endpoint: this is get_file(depth=2) reshaped. Returns
        [{"id", "name", "page", "type"}], which is what a caller needs to pick a node
        id for export_images without reading raw document JSON.
        """
        doc = await self.get_file(file_key=file_key, depth=2, tenant_id=tenant_id)
        frames: List[Dict[str, str]] = []
        canvases = ((doc.get("document") or {}).get("children") or [])
        for canvas in canvases:
            if not isinstance(canvas, dict):
                continue
            page_name = canvas.get("name", "")
            for child in canvas.get("children") or []:
                if not isinstance(child, dict):
                    continue
                if child.get("type") in {"FRAME", "COMPONENT", "COMPONENT_SET", "SECTION"}:
                    frames.append(
                        {
                            "id": child.get("id", ""),
                            "name": child.get("name", ""),
                            "page": page_name,
                            "type": child.get("type", ""),
                        }
                    )
        return frames

    async def export_images(
        self,
        file_key: str = "",
        node_ids: Optional[List[str]] = None,
        image_format: str = "png",
        scale: float = 2.0,
        tenant_id: str = "",
    ) -> Dict[str, str]:
        """GET /v1/images/{key} — render nodes and return {node_id: url}.

        Figma renders asynchronously and answers with short-lived URLs rather than
        bytes; anything that needs to keep an image must fetch and store it before the
        URL expires (~30 days).

        A node that fails to render comes back with a null URL, which is dropped here —
        a caller asking for five frames and getting four is better served by four
        working URLs than by a dict with a hole in it.
        """
        tid = tenant_id or self._tenant_id
        if not node_ids:
            raise ValueError("node_ids is required — pass at least one node id.")
        fmt = (image_format or "png").lower()
        if fmt not in {"png", "jpg", "svg", "pdf"}:
            raise ValueError(
                f"image_format must be one of png, jpg, svg, pdf — got {image_format!r}."
            )
        key = await self._target_key(file_key, tid)

        params: Dict[str, Any] = {"ids": ",".join(node_ids), "format": fmt}
        # scale applies to raster formats only; Figma rejects it on svg/pdf.
        if fmt in {"png", "jpg"}:
            params["scale"] = max(0.01, min(4.0, float(scale)))

        data, _ = await self._figma_request_with_retry(
            "GET", f"/v1/images/{key}", tid or _DEFAULT_BUCKET, params=params
        )
        if not isinstance(data, dict):
            return {}
        # Figma reports per-request failure in an `err` field with HTTP 200.
        if data.get("err"):
            raise RuntimeError(f"Figma image export failed: {data['err']}")
        images = data.get("images") or {}
        return {nid: url for nid, url in images.items() if url}
