"""Webhook verifier registry — dispatches to per-provider signature verifiers.

Mirrors the _CONNECTOR_REGISTRY pattern from config/connector_factory.py.
Unknown connector → KeyError raised by get_verifier().
"""
from __future__ import annotations

from typing import Callable

from webhooks.verifiers.github import verify_github_signature
from webhooks.verifiers.slack import verify_slack_signature
from webhooks.verifiers.jira import verify_jira_signature
from webhooks.verifiers.azure_repos import verify_ado_signature

# Registry: connector kind → verifier callable
_VERIFIER_REGISTRY: dict[str, Callable] = {
    "github": verify_github_signature,
    "slack": verify_slack_signature,
    "jira": verify_jira_signature,
    "azure_repos": verify_ado_signature,
    # GitHub Actions signs workflow_run deliveries exactly like any other GitHub
    # webhook (X-Hub-Signature-256, HMAC-SHA256 over the raw body), so the algorithm is
    # reused verbatim. Only the SECRET differs — see _invoke_verifier — so a CI webhook
    # can be rotated without touching the issues webhook.
    "github_actions": verify_github_signature,
}


def get_verifier(connector: str) -> Callable:
    """Return the verifier callable for the given connector kind.

    Raises KeyError for unknown connectors — caller should return 400.
    """
    if connector not in _VERIFIER_REGISTRY:
        raise KeyError(f"No verifier registered for connector: {connector!r}")
    return _VERIFIER_REGISTRY[connector]
