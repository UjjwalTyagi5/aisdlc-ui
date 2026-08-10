"""RED tests for webhook signature verification — REQ-M6-06.

Covers all four providers:
  - GitHub: valid HMAC-SHA256 (sha256= prefix) → ok; missing/mismatch → ValueError
  - Slack: fresh timestamp + valid sig → ok; timestamp >5 min old → ValueError
  - Jira: valid X-Hub-Signature (sha256= prefix) → ok; missing header → ValueError
  - ADO/Azure Repos: valid Basic-Auth match → ok; mismatch → ValueError

Verifiers are imported from webhooks.verifiers.{github,slack,jira,azure_repos}.
Tests skip at collection time when the webhooks package does not yet exist.

No network calls. All crypto uses stdlib hmac/hashlib + slack-sdk SignatureVerifier.
"""
import hashlib
import hmac
import sys
import time
import base64
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from webhooks.verifiers.github import verify_github_signature
    from webhooks.verifiers.slack import verify_slack_signature
    from webhooks.verifiers.jira import verify_jira_signature
    from webhooks.verifiers.azure_repos import verify_ado_signature
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.verifiers not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

_RAW_BODY = b'{"action":"opened","issue":{"id":1}}'
_GITHUB_SECRET = "github-webhook-secret-abc"
_SLACK_SECRET = "slack-signing-secret-xyz"
_JIRA_SECRET = "jira-webhook-secret-def"
_ADO_USER = "ado-user"
_ADO_PASS = "ado-webhook-pass"


def _github_sig(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _jira_sig(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _slack_sig(body: bytes, secret: str, timestamp: str) -> str:
    base_string = f"v0:{timestamp}:{body.decode('utf-8')}"
    mac = hmac.new(secret.encode(), msg=base_string.encode(), digestmod=hashlib.sha256)
    return "v0=" + mac.hexdigest()


def _basic_auth_header(user: str, pwd: str) -> str:
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return f"Basic {token}"


# ──────────────────────────────────────────────────────────────
# GitHub Signature Verification
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_github_valid_signature_does_not_raise():
    """Valid GitHub HMAC-SHA256 signature passes without raising."""
    sig = _github_sig(_RAW_BODY, _GITHUB_SECRET)
    await verify_github_signature(_RAW_BODY, sig, _GITHUB_SECRET)


@pytest.mark.unit
async def test_github_missing_signature_raises_value_error():
    """Missing X-Hub-Signature-256 header raises ValueError."""
    with pytest.raises(ValueError):
        await verify_github_signature(_RAW_BODY, None, _GITHUB_SECRET)


@pytest.mark.unit
async def test_github_wrong_signature_raises_value_error():
    """Tampered payload produces signature mismatch → ValueError."""
    sig = _github_sig(b"different-body", _GITHUB_SECRET)
    with pytest.raises(ValueError):
        await verify_github_signature(_RAW_BODY, sig, _GITHUB_SECRET)


@pytest.mark.unit
async def test_github_malformed_signature_raises_value_error():
    """Malformed signature (no sha256= prefix) raises ValueError."""
    with pytest.raises(ValueError):
        await verify_github_signature(_RAW_BODY, "notasig", _GITHUB_SECRET)


# ──────────────────────────────────────────────────────────────
# Slack Signature Verification
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_slack_valid_fresh_signature_does_not_raise():
    """Valid Slack signature with a fresh timestamp (within 5 min) passes."""
    ts = str(int(time.time()))
    sig = _slack_sig(_RAW_BODY, _SLACK_SECRET, ts)
    await verify_slack_signature(_RAW_BODY, ts, sig, _SLACK_SECRET)


@pytest.mark.unit
async def test_slack_stale_timestamp_raises_value_error():
    """Slack signature with timestamp older than 5 minutes raises ValueError (replay attack)."""
    stale_ts = str(int(time.time()) - 400)  # 400 seconds old
    sig = _slack_sig(_RAW_BODY, _SLACK_SECRET, stale_ts)
    with pytest.raises(ValueError):
        await verify_slack_signature(_RAW_BODY, stale_ts, sig, _SLACK_SECRET)


@pytest.mark.unit
async def test_slack_missing_signature_raises_value_error():
    """Missing Slack signature raises ValueError."""
    ts = str(int(time.time()))
    with pytest.raises(ValueError):
        await verify_slack_signature(_RAW_BODY, ts, None, _SLACK_SECRET)


@pytest.mark.unit
async def test_slack_wrong_signature_raises_value_error():
    """Wrong Slack signature raises ValueError."""
    ts = str(int(time.time()))
    with pytest.raises(ValueError):
        await verify_slack_signature(_RAW_BODY, ts, "v0=wrongsig", _SLACK_SECRET)


# ──────────────────────────────────────────────────────────────
# Jira Signature Verification
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_jira_valid_signature_does_not_raise():
    """Valid Jira X-Hub-Signature (sha256= prefix) passes without raising."""
    sig = _jira_sig(_RAW_BODY, _JIRA_SECRET)
    await verify_jira_signature(_RAW_BODY, sig, _JIRA_SECRET)


@pytest.mark.unit
async def test_jira_missing_signature_raises_value_error():
    """Missing X-Hub-Signature header raises ValueError."""
    with pytest.raises(ValueError):
        await verify_jira_signature(_RAW_BODY, None, _JIRA_SECRET)


@pytest.mark.unit
async def test_jira_wrong_signature_raises_value_error():
    """Tampered Jira payload raises ValueError."""
    sig = _jira_sig(b"tampered-body", _JIRA_SECRET)
    with pytest.raises(ValueError):
        await verify_jira_signature(_RAW_BODY, sig, _JIRA_SECRET)


# ──────────────────────────────────────────────────────────────
# ADO / Azure Repos Signature Verification (Basic Auth)
# ──────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_ado_valid_basic_auth_does_not_raise():
    """Valid ADO Basic-Auth Authorization header passes without raising."""
    auth_header = _basic_auth_header(_ADO_USER, _ADO_PASS)
    await verify_ado_signature(_RAW_BODY, auth_header, _ADO_USER, _ADO_PASS)


@pytest.mark.unit
async def test_ado_wrong_password_raises_value_error():
    """ADO Basic-Auth with wrong password raises ValueError."""
    auth_header = _basic_auth_header(_ADO_USER, "wrong-password")
    with pytest.raises(ValueError):
        await verify_ado_signature(_RAW_BODY, auth_header, _ADO_USER, _ADO_PASS)


@pytest.mark.unit
async def test_ado_missing_auth_header_raises_value_error():
    """Missing ADO Authorization header raises ValueError when a secret is configured."""
    with pytest.raises(ValueError):
        await verify_ado_signature(_RAW_BODY, None, _ADO_USER, _ADO_PASS)
