"""JWT encode/decode helpers — HS256 local mode and RS256 enterprise JWKS mode.

All env imports come from config.env — never os.getenv directly.
"""
import logging
import uuid as _uuid
from datetime import datetime, timedelta

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from config.env import (
    AGENT_RUNTIME_MODE,
    AUTH0_AUDIENCE,
    ENABLE_OIDC,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    OIDC_ISSUER_URL,
)

logger = logging.getLogger(__name__)

# Module-level JWKS client so key-set fetches are cached across requests (RS256 enterprise mode).
_jwks_client: PyJWKClient | None = None

# Re-fetch JWKS at most every JWKS_LIFESPAN_SECONDS so IdP signing-key rotation is
# picked up without a process restart (CR-02).
JWKS_LIFESPAN_SECONDS = 300


class JWKSUnavailableError(Exception):
    """Raised when the JWKS endpoint cannot be reached / returns a transport error.

    Deliberately NOT a subclass of jwt.InvalidTokenError: a JWKS fetch failure is an
    infrastructure/availability problem (map → 503), not a malformed-token problem
    (map → 401).  Keeping the two distinct prevents a transient IdP/network outage
    from surfacing as an unhandled 500 (CR-02).
    """


def is_enterprise_oidc() -> bool:
    """Single source of truth for the enterprise-OIDC dispatch gate (CR-01).

    Both decode_token and the FastAPI middleware MUST call this so their gates can
    never drift.  All three conditions are required: an OIDC-enabled enterprise
    deployment with no OIDC_ISSUER_URL is a misconfiguration the boot guard rejects
    (see _check_oidc_audience_guard in process_api.py), so this predicate never has
    to tolerate the half-configured state at request time.
    """
    return ENABLE_OIDC and AGENT_RUNTIME_MODE == "enterprise" and bool(OIDC_ISSUER_URL)


def create_access_token(
    user_id: str,
    tenant_id: str = "",
    expires_minutes: int = 60,
    permissions: list[str] | None = None,
) -> str:
    """Mint a signed JWT for the given user. Used by dev helper scripts and tests.

    Why permissions is resolved by the caller: resolve_permissions_for_user is async
    (it queries the DB under the tenant GUC).  Keeping this function sync avoids pulling
    an event loop into every call site.  The login/OIDC route in 7.3 will await the
    async resolver and then pass the result here (D-02, REQ-M7-12).
    No workspace claim is added here — deferred to 7.3/OIDC (D-06).
    """
    jti = str(_uuid.uuid4())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "jti": jti,
        "exp": datetime.utcnow() + timedelta(minutes=expires_minutes),
        # Default to empty list (deny-by-default) if no permissions supplied (D-02).
        "permissions": permissions or [],
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Dispatch to RS256 enterprise validation or HS256 local validation.

    ENABLE_OIDC gates the RS256 path in lockstep with the frontend login flag
    (REQ-M7-15) — the existing enterprise+OIDC_ISSUER_URL guard is extended,
    not rebuilt (D-01).  The gate is is_enterprise_oidc() so it can never drift
    from the middleware's gate (CR-01).
    """
    if is_enterprise_oidc():
        return _decode_rs256(token)
    return _decode_hs256(token)


def _decode_hs256(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])


def _decode_rs256(token: str) -> dict:
    # The decode-time AUTH0_AUDIENCE RuntimeError has moved to the lifespan startup
    # guard in process_api.py (D-03a, SC#3) — the app refuses to boot without it,
    # so the check is unnecessary here.
    global _jwks_client
    # Strip any trailing slash only for the JWKS URL (avoids a double slash).  The
    # issuer= match below uses the RAW OIDC_ISSUER_URL because Auth0 iss claims
    # canonically include the trailing slash (WR-01 is tracked separately).
    issuer = OIDC_ISSUER_URL.rstrip("/")
    if not issuer.startswith("https://"):
        # Refuse to fetch signing keys over an unauthenticated channel — an http://
        # or attacker-influenced issuer is a key-substitution / MITM path that
        # defeats RS256 entirely (CR-02).
        raise RuntimeError(
            "OIDC_ISSUER_URL must be https:// — refusing to fetch JWKS over an insecure channel"
        )
    if _jwks_client is None:
        # Auth0-broker standard JWKS path — NOT the Azure /discovery/v2.0/keys path
        # (which 404s against an Auth0 issuer, Pitfall 3).  lifespan re-fetches the
        # key set periodically so IdP key rotation does not break logins until a
        # process restart (CR-02).
        _jwks_client = PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=JWKS_LIFESPAN_SECONDS,
        )
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
    except PyJWKClientError as exc:
        # JWKS fetch / parse / kid-not-found failure.  This is NOT an InvalidTokenError
        # subclass, so re-raise as JWKSUnavailableError for the middleware to map to a
        # clean 503 rather than letting it escape as an unhandled 500 (CR-02).
        raise JWKSUnavailableError(str(exc)) from exc
    except Exception as exc:
        # Connection/timeout errors from the JWKS HTTP fetch are also availability
        # failures, not token-validity failures — surface them as 503 too (CR-02).
        raise JWKSUnavailableError(str(exc)) from exc
    return jwt.decode(
        token,
        signing_key.key,            # PyJWK.key object — never a raw JWK string (CVE-2026-48526)
        algorithms=["RS256"],       # Single algorithm — NEVER ["RS256", "HS256"] (CVE-2026-48526)
        audience=AUTH0_AUDIENCE,
        issuer=OIDC_ISSUER_URL,
        # iss and aud are REQUIRED, not merely verified-when-present (WR-02): a token
        # lacking iss/aud entirely must be rejected (cross-IdP token confusion, T-7.3-05).
        # tenant_id is DELIBERATELY absent — PyJWT require[] checks FLAT top-level
        # claims but the real Auth0 token carries tenant_id NESTED under
        # https://sdlc/tenant (session.ts:30-32).  Adding "tenant_id" here →
        # MissingRequiredClaimError on every enterprise login.  The nested→flat
        # extraction is done by extract_tenant_id in the middleware.
        options={"require": ["exp", "iat", "sub", "iss", "aud"]},
    )
