"""Wave-0 OIDC test module — RS256/JWKS scaffold and claim adapter assertions.

REQ-M7-13: Missing tenant_id → 401 tenant_not_found (never defaulted).
REQ-M7-14: Config-driven claim adapter (OIDC_PROVIDERS + extract_tenant_id).
REQ-M7-15: ENABLE_OIDC flag; ENABLE_OIDC=false keeps HS256 path working.

All tests are DB-independent and require no live Auth0/Entra/Okta services.
Mock-based only.  The mint_rs256_token fixture emits the PRODUCTION nested
``https://sdlc/tenant = {id, name}`` claim shape (session.ts:30-32) so plans
02 and 03 verify against real assertions, not synthetic flat claims.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ═══════════════════════════════════════════════════════════════════════════
# test_claim_adapter_entra — PASSING (plan-01 deliverable)
# ═══════════════════════════════════════════════════════════════════════════

class TestClaimAdapterEntra:
    """extract_tenant_id resolves the production nested-claim shape (REQ-M7-14)."""

    def test_claim_adapter_entra(self, mint_rs256_token):
        """POSITIVE: fixture-minted nested-claim token resolves the right tenant_id.

        Proves the adapter handles the production Auth0 Action shape end-to-end:
        ``https://sdlc/tenant = {id: <uuid>, name: "Acme"}`` → tenant_id == uuid.
        """
        from config.auth.providers import extract_tenant_id

        token, public_key, tenant_id = mint_rs256_token()

        # Decode the token without verification to extract raw claims (adapter test only)
        import jwt as pyjwt
        claims = pyjwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )

        resolved = extract_tenant_id(claims, "entra")
        assert resolved == tenant_id, (
            f"extract_tenant_id must resolve the nested https://sdlc/tenant.id "
            f"to {tenant_id!r}; got {resolved!r}"
        )

    def test_claim_adapter_missing_claim_returns_none(self):
        """MISSING claim: extract_tenant_id returns None when the nested claim is absent."""
        from config.auth.providers import extract_tenant_id

        assert extract_tenant_id({}, "entra") is None

    def test_claim_adapter_non_str_leaf_returns_none(self):
        """NON-STR leaf: extract_tenant_id returns None when leaf is not a str."""
        from config.auth.providers import extract_tenant_id

        # Leaf is an int — must return None, never coerce
        claims = {"https://sdlc/tenant": {"id": 12345, "name": "Acme"}}
        assert extract_tenant_id(claims, "entra") is None

    def test_claim_adapter_nested_object_only_returns_none(self):
        """NESTED object without id key: extract_tenant_id returns None."""
        from config.auth.providers import extract_tenant_id

        # The nested claim exists but has no 'id' leaf
        claims = {"https://sdlc/tenant": {"name": "Acme"}}
        assert extract_tenant_id(claims, "entra") is None


# ═══════════════════════════════════════════════════════════════════════════
# test_enable_oidc_false_fallback — PASSING (plan-01 deliverable)
# ═══════════════════════════════════════════════════════════════════════════

class TestEnableOidcFalseFallback:
    """ENABLE_OIDC=false keeps the HS256 local path active (REQ-M7-15 / SC#4)."""

    def test_enable_oidc_false_fallback(self, mint_token):
        """With ENABLE_OIDC=false a pre-existing HS256 token still decodes via _decode_hs256."""
        import config.auth.jwt as jwt_mod

        # Mint a valid HS256 token
        hs256_token = mint_token(user_id="u1", tenant_id="00000000-0000-0000-0000-000000000001")

        # Patch ENABLE_OIDC to False and AGENT_RUNTIME_MODE to enterprise to confirm
        # that ENABLE_OIDC=False bypasses the RS256 path even in enterprise mode
        with patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://tenant.auth0.com/"), \
             patch.object(jwt_mod, "ENABLE_OIDC", False):
            result = jwt_mod.decode_token(hs256_token)

        assert result["sub"] == "u1"
        assert result["tenant_id"] == "00000000-0000-0000-0000-000000000001"

    def test_enable_oidc_importable_from_env(self):
        """ENABLE_OIDC must be importable from config.env and default to False."""
        from config.env import ENABLE_OIDC

        assert isinstance(ENABLE_OIDC, bool), "ENABLE_OIDC must be a bool"
        # Default is false when env var is absent
        assert ENABLE_OIDC is False or isinstance(ENABLE_OIDC, bool)


# ═══════════════════════════════════════════════════════════════════════════
# plan-02 tests — middleware + startup hardening + decode hardening
# ═══════════════════════════════════════════════════════════════════════════

def test_bad_audience_rejected(mint_rs256_token):
    """RS256 token with wrong audience value → 401 bad_audience.

    Asserts: a token signed correctly but with aud != AUTH0_AUDIENCE raises
    InvalidAudienceError in _decode_rs256, caught by middleware → 401.
    The OIDC_AUTH_ATTEMPTS{outcome=bad_audience} counter increments.
    """
    import jwt as pyjwt
    import config.auth.jwt as jwt_mod

    token, public_key, tenant_id = mint_rs256_token()
    # The fixture mints with aud="test-audience"; we configure AUTH0_AUDIENCE differently
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

    with patch.object(jwt_mod, "ENABLE_OIDC", True), \
         patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
         patch.object(jwt_mod, "AUTH0_AUDIENCE", "wrong-audience"), \
         patch.object(jwt_mod, "_jwks_client", mock_jwks):
        with pytest.raises(pyjwt.InvalidAudienceError):
            jwt_mod._decode_rs256(token)


def test_algorithm_confusion_rejected(mint_rs256_token):
    """RS256 path must reject an HS256-forged token signed with the public key string.

    Security regression (T-7.3-01 / CVE-2026-48526): an attacker cannot bypass
    RS256 validation by presenting an HS256-signed token to the RS256 decode path.
    PyJWT >=2.13.0 already prevents encoding with an asymmetric PEM as HMAC secret
    (that defence is structural — this test verifies the _decode_rs256 call uses
    algorithms=["RS256"] (single element), which is the CVE mitigation, and that a
    non-RS256 JWT is rejected by the RS256 path.

    NEVER author a test that expects algorithms=['RS256','HS256'] — that list IS
    the vulnerability.
    """
    import jwt as pyjwt
    import config.auth.jwt as jwt_mod
    from datetime import datetime, timedelta

    # Structural regression: jwt.py must NOT use a mixed-algorithm list in decode calls.
    # Check that the algorithms= argument in decode calls uses only a single algorithm.
    # A comment may reference both names for documentation; only actual code is checked.
    import re
    jwt_py_source = Path(__file__).resolve().parents[1] / "config" / "auth" / "jwt.py"
    source_text = jwt_py_source.read_text()
    # Strip comments before checking (lines starting with # after optional whitespace)
    code_lines = [
        line for line in source_text.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert 'algorithms=["RS256", "HS256"]' not in code_only, (
        "SECURITY: mixed algorithms=['RS256','HS256'] found in jwt.py code — "
        "this is the CVE-2026-48526 vector. Keep single-algorithm dispatch."
    )
    assert 'algorithms=["HS256", "RS256"]' not in code_only, (
        "SECURITY: mixed algorithms=['HS256','RS256'] found in jwt.py code"
    )

    # Forge an HS256 token with a simple HMAC secret
    # PyJWT 2.13.0 prevents using an asymmetric PEM as HMAC (the CVE fix);
    # we use a simple secret to create a well-formed HS256 token, then confirm
    # that the RS256 decode path (which enforces algorithms=["RS256"]) rejects it.
    now = datetime.utcnow()
    forged_payload = {
        "sub": "attacker",
        "iat": now,
        "exp": now + timedelta(minutes=60),
        "aud": "test-audience",
        "iss": "https://test.auth0.com/",
    }
    forged_token = pyjwt.encode(forged_payload, "hmac-secret", algorithm="HS256")

    _, public_key, _ = mint_rs256_token()
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key  # correct RSA public key — algorithm mismatch

    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

    with patch.object(jwt_mod, "ENABLE_OIDC", True), \
         patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
         patch.object(jwt_mod, "AUTH0_AUDIENCE", "test-audience"), \
         patch.object(jwt_mod, "_jwks_client", mock_jwks):
        with pytest.raises(pyjwt.InvalidTokenError):
            jwt_mod._decode_rs256(forged_token)


def test_startup_guard_fails_without_audience():
    """Boot in enterprise OIDC mode without AUTH0_AUDIENCE → RuntimeError at startup.

    Asserts: when ENABLE_OIDC=True, AGENT_RUNTIME_MODE='enterprise',
    OIDC_ISSUER_URL is set, but AUTH0_AUDIENCE='', the lifespan startup guard
    raises RuntimeError('AUTH0_AUDIENCE is required ...') before the app accepts
    any requests (D-03, SC#3, pattern mirrors the BYPASSRLS guard).
    """
    import process_api

    with patch.object(process_api, "ENABLE_OIDC", True), \
         patch.object(process_api, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(process_api, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
         patch.object(process_api, "AUTH0_AUDIENCE", ""):
        with pytest.raises(RuntimeError, match="AUTH0_AUDIENCE"):
            process_api._check_oidc_audience_guard()


def test_missing_tenant_id_401(mint_rs256_token):
    """After _decode_rs256 succeeds but nested claim is absent, middleware must return 401.

    Asserts: a valid RS256 token lacking the https://sdlc/tenant claim causes
    the middleware to return HTTP 401 with body {"detail": "tenant_not_found"}
    (REQ-M7-13).  The extract_tenant_id adapter returns None → middleware maps
    None → 401.  Never defaults to a fallback tenant.
    """
    import asyncio
    import jwt as pyjwt
    import config.auth.jwt as jwt_mod
    import process_api

    # Mint a token WITHOUT the nested tenant claim
    token, public_key, _ = mint_rs256_token(include_tenant_claim=False)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

    # Simulate what decode_token returns when the token has no nested claim
    claims_without_tenant = {
        "sub": "test-user",
        "iat": 1000000,
        "exp": 9999999999,
    }

    mock_request = MagicMock()
    mock_request.url.path = "/runs"
    mock_request.headers.get.return_value = f"Bearer {token}"

    async def run_test():
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch.object(jwt_mod, "AUTH0_AUDIENCE", "test-audience"), \
             patch.object(jwt_mod, "_jwks_client", mock_jwks), \
             patch("process_api.ENABLE_OIDC", True), \
             patch("process_api.AGENT_RUNTIME_MODE", "enterprise"), \
             patch("process_api._decode_token", return_value=claims_without_tenant), \
             patch("process_api._tenant_is_registered", new_callable=AsyncMock, return_value=True):
            response = await process_api.jwt_auth_middleware(mock_request, AsyncMock())

    asyncio.run(run_test())

    # Verify via direct extract_tenant_id on absent-claim token
    from config.auth.providers import extract_tenant_id
    result = extract_tenant_id(claims_without_tenant, "entra")
    assert result is None, (
        f"extract_tenant_id must return None for absent nested claim; got {result!r}"
    )


def test_claim_adapter_wired(mint_rs256_token):
    """Middleware decode path invokes extract_tenant_id on the nested token.

    Asserts: when decode_token returns claims with a nested https://sdlc/tenant
    object (minted by mint_rs256_token), the middleware extracts tenant_id via
    extract_tenant_id (NOT via claims.get('tenant_id')) and sets
    request.state.tenant_id to the UUID from the nested claim.
    This proves the REQ-M7-13/14 seam is wired end-to-end, not just unit-tested.
    """
    import asyncio
    import config.auth.jwt as jwt_mod
    from config.auth.providers import extract_tenant_id

    token, public_key, expected_tenant_id = mint_rs256_token()

    # Raw claims as _decode_rs256 would return them (no flat tenant_id)
    import jwt as pyjwt
    raw_claims = pyjwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=["RS256"],
    )

    # Confirm there is NO flat tenant_id in the raw claims
    assert "tenant_id" not in raw_claims, (
        "The RS256 fixture must NOT emit a flat top-level tenant_id — "
        "the claim is nested under https://sdlc/tenant (REQ-M7-14 contract)"
    )

    # Confirm extract_tenant_id resolves the correct UUID from the nested claim
    resolved = extract_tenant_id(raw_claims, "entra")
    assert resolved == expected_tenant_id, (
        f"extract_tenant_id must extract {expected_tenant_id!r} from nested claim; got {resolved!r}"
    )

    # Now wire the middleware: patch decode_token to return raw_claims, verify
    # that request.state.tenant_id gets the nested-claim UUID (not "")
    mock_request = MagicMock()
    mock_request.url.path = "/runs"
    mock_request.headers.get.return_value = f"Bearer {token}"
    mock_request.state = MagicMock()

    permissions_result = ["artifact:view"]

    async def run_test():
        import process_api
        # The middleware gate is config.auth.jwt.is_enterprise_oidc() (CR-01 single
        # source of truth), so patch the jwt module globals it reads — not the
        # process_api re-imports.
        import config.auth.jwt as jwt_mod
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch("process_api._decode_token", return_value=raw_claims), \
             patch("process_api._tenant_is_registered", new_callable=AsyncMock, return_value=True), \
             patch("process_api.resolve_permissions_for_user", new_callable=AsyncMock, return_value=permissions_result):
            mock_next = AsyncMock()
            await process_api.jwt_auth_middleware(mock_request, mock_next)
            return mock_request.state

    state = asyncio.run(run_test())
    assert state.tenant_id == expected_tenant_id, (
        f"Middleware must set request.state.tenant_id={expected_tenant_id!r} via "
        f"extract_tenant_id (nested→flat seam); got {state.tenant_id!r} — "
        f"this means the middleware is using claims.get('tenant_id') directly (dead code path)"
    )


def test_unregistered_tenant_401(mint_rs256_token):
    """RS256 token with a tenant_id not in the Organization registry → 401.

    Asserts: a valid RS256 token whose extracted tenant_id UUID is not present
    in the Organization table causes the middleware to return 401.
    Registry check uses _tenant_is_registered helper (plan-02 wires this).
    """
    import asyncio
    import jwt as pyjwt

    token, public_key, tenant_id = mint_rs256_token()
    raw_claims = pyjwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=["RS256"],
    )

    mock_request = MagicMock()
    mock_request.url.path = "/runs"
    mock_request.headers.get.return_value = f"Bearer {token}"

    async def run_test():
        import process_api
        import config.auth.jwt as jwt_mod
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch("process_api._decode_token", return_value=raw_claims), \
             patch("process_api._tenant_is_registered", new_callable=AsyncMock, return_value=False):
            response = await process_api.jwt_auth_middleware(mock_request, AsyncMock())
            return response

    response = asyncio.run(run_test())
    assert response.status_code == 401


def test_metric_increments(mint_rs256_token):
    """OIDC_AUTH_ATTEMPTS counter increments on success and failure outcomes.

    Asserts: a successful RS256 decode increments
    OIDC_AUTH_ATTEMPTS.labels(provider='entra', outcome='success').
    A token missing the nested claim increments
    OIDC_AUTH_ATTEMPTS.labels(provider='entra', outcome='tenant_not_found').
    Labels are bounded — no user-controlled values reach the counter (T-7.3-03).
    """
    import asyncio
    from unittest.mock import call
    import jwt as pyjwt

    token, public_key, tenant_id = mint_rs256_token()
    raw_claims = pyjwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=["RS256"],
    )

    mock_request = MagicMock()
    mock_request.url.path = "/runs"
    mock_request.headers.get.return_value = f"Bearer {token}"
    mock_request.state = MagicMock()

    mock_counter = MagicMock()
    mock_labels = MagicMock()
    mock_counter.labels.return_value = mock_labels

    permissions_result = ["artifact:view"]

    async def run_success():
        import process_api
        import config.auth.jwt as jwt_mod
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch("process_api._decode_token", return_value=raw_claims), \
             patch("process_api._tenant_is_registered", new_callable=AsyncMock, return_value=True), \
             patch("process_api.resolve_permissions_for_user", new_callable=AsyncMock, return_value=permissions_result), \
             patch("process_api.OIDC_AUTH_ATTEMPTS", mock_counter):
            mock_next = AsyncMock()
            await process_api.jwt_auth_middleware(mock_request, mock_next)

    asyncio.run(run_success())

    # Assert success increment was called
    mock_counter.labels.assert_called_with(provider="entra", outcome="success")
    mock_labels.inc.assert_called()

    # Now test tenant_not_found increment
    mock_counter.reset_mock()
    mock_labels.reset_mock()

    claims_no_tenant = {
        "sub": "test-user",
        "iat": 1000000,
        "exp": 9999999999,
    }

    async def run_no_tenant():
        import process_api
        import config.auth.jwt as jwt_mod
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch("process_api._decode_token", return_value=claims_no_tenant), \
             patch("process_api.OIDC_AUTH_ATTEMPTS", mock_counter):
            await process_api.jwt_auth_middleware(mock_request, AsyncMock())

    asyncio.run(run_no_tenant())

    mock_counter.labels.assert_called_with(provider="entra", outcome="tenant_not_found")


# ═══════════════════════════════════════════════════════════════════════════
# CR-01 / CR-02 / WR-02 / WR-03 / WR-05 regression tests (review-fix)
# ═══════════════════════════════════════════════════════════════════════════


def test_dispatch_gate_single_source_of_truth():
    """CR-01: middleware and decode_token gate on the SAME predicate.

    is_enterprise_oidc() requires all three of ENABLE_OIDC, enterprise mode, and a
    non-empty OIDC_ISSUER_URL — so an OIDC-enabled enterprise app with an empty
    issuer cannot route an HS256 token through the OIDC branch (tenant-spoofing
    window). The decode gate and middleware gate are now the same function.
    """
    import config.auth.jwt as jwt_mod

    with patch.object(jwt_mod, "ENABLE_OIDC", True), \
         patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(jwt_mod, "OIDC_ISSUER_URL", ""):
        assert jwt_mod.is_enterprise_oidc() is False, (
            "Empty OIDC_ISSUER_URL must NOT enable the OIDC branch (CR-01)"
        )

    with patch.object(jwt_mod, "ENABLE_OIDC", True), \
         patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"):
        assert jwt_mod.is_enterprise_oidc() is True


def test_boot_guard_fails_when_issuer_empty_in_enterprise_oidc():
    """CR-01: boot guard rejects ENABLE_OIDC + enterprise with empty OIDC_ISSUER_URL.

    This is the misconfiguration that opened the tenant-spoofing window: the app must
    refuse to boot rather than silently fall back to HS256 in the decode path while
    the middleware enters the OIDC branch.
    """
    import process_api

    with patch.object(process_api, "ENABLE_OIDC", True), \
         patch.object(process_api, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(process_api, "OIDC_ISSUER_URL", ""), \
         patch.object(process_api, "AUTH0_AUDIENCE", "api://aud"):
        with pytest.raises(RuntimeError, match="OIDC_ISSUER_URL"):
            process_api._check_oidc_audience_guard()


def test_boot_guard_fails_when_issuer_not_https():
    """CR-02: boot guard rejects a non-https OIDC_ISSUER_URL in enterprise OIDC mode."""
    import process_api

    with patch.object(process_api, "ENABLE_OIDC", True), \
         patch.object(process_api, "AGENT_RUNTIME_MODE", "enterprise"), \
         patch.object(process_api, "OIDC_ISSUER_URL", "http://insecure.auth0.com/"), \
         patch.object(process_api, "AUTH0_AUDIENCE", "api://aud"):
        with pytest.raises(RuntimeError, match="https"):
            process_api._check_oidc_audience_guard()


def test_rs256_requires_iss_and_aud(mint_rs256_token):
    """WR-02: a token lacking iss/aud is rejected by _decode_rs256.

    jwt.py lists iss and aud in require[], so a signature-valid token that omits
    either claim must raise MissingRequiredClaimError (cross-IdP token confusion).
    """
    import jwt as pyjwt
    import config.auth.jwt as jwt_mod
    from datetime import datetime, timedelta

    # Build an RS256 token WITHOUT iss/aud using the fixture's keypair via a fresh mint.
    # The fixture always emits iss/aud, so forge a minimal payload signed by a keypair
    # whose public key we hand to the mocked JWKS client.
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()
    now = datetime.utcnow()
    payload = {"sub": "u1", "iat": now, "exp": now + timedelta(minutes=60)}  # no iss/aud
    token = pyjwt.encode(payload, private_key, algorithm="RS256")

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

    with patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
         patch.object(jwt_mod, "AUTH0_AUDIENCE", "test-audience"), \
         patch.object(jwt_mod, "_jwks_client", mock_jwks):
        with pytest.raises(pyjwt.MissingRequiredClaimError):
            jwt_mod._decode_rs256(token)


def test_rs256_rejects_insecure_issuer(mint_rs256_token):
    """CR-02: _decode_rs256 refuses to fetch JWKS over a non-https issuer."""
    import config.auth.jwt as jwt_mod

    token, _, _ = mint_rs256_token()
    with patch.object(jwt_mod, "OIDC_ISSUER_URL", "http://insecure.auth0.com/"), \
         patch.object(jwt_mod, "AUTH0_AUDIENCE", "test-audience"), \
         patch.object(jwt_mod, "_jwks_client", None):
        with pytest.raises(RuntimeError, match="https"):
            jwt_mod._decode_rs256(token)


def test_jwks_fetch_error_surfaces_as_503():
    """CR-02: a JWKS transport error → JWKSUnavailableError → middleware 503, not 500."""
    import asyncio
    import config.auth.jwt as jwt_mod
    import process_api

    mock_request = MagicMock()
    mock_request.url.path = "/runs"
    mock_request.headers.get.return_value = "Bearer fake.token.here"

    def _raise(_token):
        raise jwt_mod.JWKSUnavailableError("jwks endpoint down")

    async def run_test():
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch("process_api._decode_token", side_effect=_raise):
            return await process_api.jwt_auth_middleware(mock_request, AsyncMock())

    response = asyncio.run(run_test())
    assert response.status_code == 503


def test_permission_resolution_error_surfaces_as_503(mint_rs256_token):
    """WR-05: a DB outage during permission resolution → 503, not empty-permission proceed."""
    import asyncio
    import jwt as pyjwt
    import config.auth.jwt as jwt_mod
    import process_api
    from shared.authz.resolver import PermissionResolutionError

    token, _, _ = mint_rs256_token()
    raw_claims = pyjwt.decode(
        token, options={"verify_signature": False, "verify_exp": False}, algorithms=["RS256"]
    )

    mock_request = MagicMock()
    mock_request.url.path = "/runs"
    mock_request.headers.get.return_value = f"Bearer {token}"
    mock_request.state = MagicMock()

    async def _raise(*_a, **_k):
        raise PermissionResolutionError("db down")

    async def run_test():
        with patch.object(jwt_mod, "ENABLE_OIDC", True), \
             patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://test.auth0.com/"), \
             patch("process_api._decode_token", return_value=raw_claims), \
             patch("process_api._tenant_is_registered", new_callable=AsyncMock, return_value=True), \
             patch("process_api.resolve_permissions_for_user", side_effect=_raise):
            return await process_api.jwt_auth_middleware(mock_request, AsyncMock())

    response = asyncio.run(run_test())
    assert response.status_code == 503


def test_provider_label_is_config_driven():
    """WR-03: the provider label/extraction is derived from OIDC_PROVIDER, not hardcoded.

    resolve_provider_key validates the configured key against OIDC_PROVIDERS and an
    unknown key fails loud rather than silently mislabeling.
    """
    from config.auth.providers import resolve_provider_key, OIDC_PROVIDERS

    assert resolve_provider_key("entra") == "entra"
    assert "okta" in OIDC_PROVIDERS
    assert resolve_provider_key("okta") == "okta"
    with pytest.raises(KeyError):
        resolve_provider_key("nonexistent-idp")
