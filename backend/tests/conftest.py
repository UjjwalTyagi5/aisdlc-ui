"""Root-level test fixtures for agentic_app tests.

Provides:
  mint_token       — callable fixture that mints a real signed HS256 JWT with a
                     controllable permissions array.  Used by negative-authz tests
                     so they exercise the 403 branch rather than the 401 branch that
                     a literal invalid-token string would trigger (Open Question 1
                     resolution, milestone-7.2 RESEARCH).
  mint_rs256_token — callable fixture that generates an RSA keypair, signs an RS256
                     JWT emitting the PRODUCTION nested ``https://sdlc/tenant``
                     claim (session.ts:30-32), and returns the token + public key
                     material for JWKS mocking.  Wave-0 OIDC test scaffold
                     (milestone-7.3 plan-01).
"""
from __future__ import annotations

import pytest

from config.env import JWT_SECRET_KEY


@pytest.fixture
def mint_token():
    """Return a callable that produces a real signed HS256 JWT.

    Why a real token: a literal string like 'wrong-role-developer-token' is not
    a valid JWT — the middleware would 401, not 403.  Negative-authz tests need
    a decodable token that simply lacks the required permission (T-7.2-04).

    Usage:
        token = mint_token(user_id="u1", tenant_id="tid-abc", permissions=["run:create"])
        headers = {"Authorization": f"Bearer {token}"}
    """
    from datetime import datetime, timedelta

    import jwt as pyjwt

    def _mint(
        user_id: str = "test-user-001",
        tenant_id: str = "00000000-0000-0000-0000-000000000001",
        permissions: list[str] | None = None,
    ) -> str:
        payload = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "permissions": permissions if permissions is not None else [],
            "exp": datetime.utcnow() + timedelta(minutes=60),
        }
        return pyjwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")

    return _mint


@pytest.fixture
def mint_rs256_token():
    """Return a callable that produces a real RS256-signed JWT with the PRODUCTION nested claim.

    Why nested claim: the Auth0 Action injects ``https://sdlc/tenant = {id, name}``
    (confirmed apps/web/lib/auth/session.ts:30-32).  There is NO flat top-level
    ``tenant_id`` in the real Auth0 token — the extract_tenant_id adapter (providers.py)
    is the only extraction path (T-7.3-02 seam).

    Returns a callable with signature:
        mint(
            user_id: str = "test-user-001",
            tenant_id: str = "00000000-0000-0000-0000-000000000001",
            tenant_name: str = "Acme",
            include_tenant_claim: bool = True,
        ) -> tuple[str, public_key_object, str]

    Return shape: (token: str, public_key, tenant_id: str)
      - token       — 3-segment RS256 JWT string
      - public_key  — cryptography RSAPublicKey object; use as mock_signing_key.key in tests
      - tenant_id   — the UUID string injected into the nested claim (for assertion convenience)

    The same RSA keypair is generated once per test invocation (function scope).
    Reset ``_jwks_client`` global between RS256 cases to prevent cross-test leakage
    (Pitfall 5): ``patch.object(jwt_mod, "_jwks_client", None)``.

    Usage:
        token, public_key, tenant_id = mint_rs256_token()
        mock_key = MagicMock(); mock_key.key = public_key
        with patch.object(jwt_mod, "_jwks_client", mock_jwks):
            ...
    """
    from datetime import datetime, timedelta

    import jwt as pyjwt
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate a fresh RSA keypair for this test invocation
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    public_key = private_key.public_key()

    def _mint(
        user_id: str = "test-user-001",
        tenant_id: str = "00000000-0000-0000-0000-000000000001",
        tenant_name: str = "Acme",
        include_tenant_claim: bool = True,
    ):
        """Mint an RS256 JWT.

        When include_tenant_claim=False the nested https://sdlc/tenant key is
        omitted entirely — used to exercise the missing-claim → None path
        (test_missing_tenant_id_401 in plan-02).
        """
        now = datetime.utcnow()
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=60),
            "aud": "test-audience",
            "iss": "https://test.auth0.com/",
        }
        if include_tenant_claim:
            # PRODUCTION Auth0 Action nested claim shape (session.ts:30-32).
            # Do NOT emit a flat tenant_id — the adapter is the only extraction path.
            payload["https://sdlc/tenant"] = {"id": tenant_id, "name": tenant_name}

        token = pyjwt.encode(payload, private_key, algorithm="RS256")
        return token, public_key, tenant_id

    return _mint
