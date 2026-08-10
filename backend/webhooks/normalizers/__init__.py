"""Webhook normalizer registry — dispatches to per-provider event normalizers.

Mirrors the _CONNECTOR_REGISTRY / _VERIFIER_REGISTRY dispatch pattern.
Unknown connector → KeyError raised by get_normalizer().
"""
from __future__ import annotations

from typing import Callable

from webhooks.normalizers.github import normalize_github_event
from webhooks.normalizers.jira import normalize_jira_event
from webhooks.normalizers.azure_repos import normalize_azure_repos_event

# Registry: connector kind → normalizer callable
_NORMALIZER_REGISTRY: dict[str, Callable] = {
    "github": normalize_github_event,
    "jira": normalize_jira_event,
    "azure_repos": normalize_azure_repos_event,
}


def get_normalizer(connector: str) -> Callable:
    """Return the normalizer callable for the given connector kind.

    Raises KeyError for unknown connectors — caller should return 400.
    """
    if connector not in _NORMALIZER_REGISTRY:
        raise KeyError(f"No normalizer registered for connector: {connector!r}")
    return _NORMALIZER_REGISTRY[connector]
