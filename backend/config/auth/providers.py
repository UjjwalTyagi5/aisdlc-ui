"""OIDC provider configuration and claim adapter (REQ-M7-14).

Maps IdP identifiers to their per-provider tenant_id claim path.  New IdPs
(e.g. Okta, Ping) are added here as config entries — no code branches per IdP.

The Auth0 Action injects a NESTED ``https://sdlc/tenant = {id, name}`` object
(confirmed by apps/web/lib/auth/session.ts:30-32).  The dotted
``tenant_id_claim_path`` bridges that nested claim to a flat tenant_id string,
which is the REQ-M7-13/14 central seam consumed by the plan-02 middleware.
There is NO flat top-level ``tenant_id`` claim in the real Auth0 token — the
adapter is the only extraction path.

``provider`` values are bounded to ``OIDC_PROVIDERS.keys()`` so they are
safe to use directly as the low-cardinality Prometheus ``provider`` label.
"""
from __future__ import annotations

# Live-validated = Entra ID (D-M7-03).  Okta is config-only; no live test org.
OIDC_PROVIDERS: dict[str, dict[str, str]] = {
    "entra": {"tenant_id_claim_path": "https://sdlc/tenant.id"},
    "okta":  {"tenant_id_claim_path": "https://sdlc/tenant.id"},
}


def resolve_provider_key(provider: str) -> str:
    """Validate a configured provider key against OIDC_PROVIDERS (WR-03).

    The middleware metric/extraction label must be drawn from a server-controlled,
    bounded set (OIDC_PROVIDERS.keys()) so it stays low-cardinality and can never be
    influenced by token contents (T-7.3-03).  Raises KeyError if the configured
    OIDC_PROVIDER does not name a known provider — a boot-time misconfiguration that
    must fail loud rather than silently mislabel/mis-extract.
    """
    if provider not in OIDC_PROVIDERS:
        raise KeyError(
            f"OIDC_PROVIDER={provider!r} is not a configured provider; "
            f"expected one of {sorted(OIDC_PROVIDERS.keys())}"
        )
    return provider


def extract_tenant_id(claims: dict, provider: str) -> str | None:
    """Extract tenant_id from decoded JWT claims using the provider's configured path.

    Walks the dotted ``tenant_id_claim_path`` through the claims dict.  The
    first path segment is the full custom-namespace claim key (e.g.
    ``https://sdlc/tenant``), subsequent segments descend into nested objects.

    Returns the leaf string if found; ``None`` if the path is absent or the
    leaf is not a str — the consuming middleware maps ``None`` → 401, never
    defaulting to a fallback tenant (T-7.3-02).
    """
    path = OIDC_PROVIDERS[provider]["tenant_id_claim_path"]
    node: object = claims
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)  # type: ignore[assignment]
    return node if isinstance(node, str) else None
