"""A failed probe must say WHY, not just that it failed.

Every failure used to surface as "Key rejected — verification failed", including
the ones where the request never reached the provider at all. Reported twice
against a real Anthropic key that was fine: an API base holding a bare string
(no scheme) makes httpx build a relative URL and the call dies locally, and an
API base pointing at another vendor's endpoint answers 404. In both cases the key
was never tested, and the message sent the user to re-issue a good credential.
"""
import pytest

from shared.services.model_config import _probe_failure_reason


class _Err(Exception):
    """Stand-in whose class NAME carries the signal, as litellm's do."""


def _exc(name: str, message: str) -> Exception:
    return type(name, (_Err,), {})(message)


def test_a_bare_api_base_is_named_as_a_url_problem():
    """The exact shape seen live: litellm reports the relative path it tried."""
    exc = _exc(
        "InternalServerError",
        "litellm.InternalServerError: AnthropicException - /someone@example.com/v1/messages.",
    )

    reason = _probe_failure_reason(exc)

    assert "API base" in reason
    assert "https://" in reason
    assert "key" not in reason.lower()


def test_a_wrong_vendor_endpoint_is_named_as_a_model_problem():
    exc = _exc(
        "NotFoundError",
        'litellm.NotFoundError: AnthropicException - {"error":{"code":"404","message": "Resource not found"}}',
    )

    reason = _probe_failure_reason(exc)

    assert "API base" in reason
    assert "404" in reason


def test_a_genuinely_bad_key_still_says_so():
    """The classifier must not swing the other way and excuse every failure."""
    exc = _exc(
        "AuthenticationError",
        'litellm.AuthenticationError: AnthropicException - {"type":"error",'
        '"error":{"type":"authentication_error","message":"API key is invalid."}}',
    )

    assert _probe_failure_reason(exc) == "The provider rejected this key."


def test_a_permission_failure_is_distinguished_from_a_bad_key():
    exc = _exc("PermissionDeniedError", "permission_error: not allowed for this model")

    reason = _probe_failure_reason(exc)

    assert "valid" in reason and "permitted" in reason


def test_a_rate_limit_says_to_retry_rather_than_blaming_the_key():
    exc = _exc("RateLimitError", "litellm.RateLimitError: rate limit exceeded")

    reason = _probe_failure_reason(exc)

    assert "rate-limited" in reason
    assert "test again" in reason


def test_an_unreachable_endpoint_is_named_as_such():
    exc = _exc("APIConnectionError", "connection refused")

    assert "Could not reach" in _probe_failure_reason(exc)


def test_an_unrecognised_failure_still_returns_something_actionable():
    reason = _probe_failure_reason(_exc("WeirdProviderError", "something new"))

    assert "WeirdProviderError" in reason


@pytest.mark.parametrize("secret", ["sk-ant-super-secret", "hunter2"])
def test_no_reason_ever_echoes_the_credential(secret):
    """Error text can contain anything; the reason we render must not leak a key."""
    exc = _exc("AuthenticationError", f"bad key {secret}")

    assert secret not in _probe_failure_reason(exc)
