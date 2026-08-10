"""OAuth start URL builder for Wave C self-serve connector onboarding.

Constructs provider-specific OAuth 2.0 authorization URLs with CSRF-safe
state parameters bound to tenant_id (T-7.4-16, T-7.4-17).

Supported providers:
- jira:        Atlassian OAuth 2.0 (3LO) — live (REQ-M7-20)
- github:      GitHub App user-auth flow — built + live-deferred (REQ-M7-21)
- slack:       Slack OAuth v2 — built + live-deferred (REQ-M7-21)
- azure_repos: Azure AD OAuth 2.0 — built + live-deferred (REQ-M7-21, [ASSUMED] A3)

All env vars imported from config.env — never os.getenv.
"""
from __future__ import annotations

import urllib.parse

from config.env import (
    AGENTIC_BASE_URL,
    GITHUB_APP_ID,
    JIRA_OAUTH_CLIENT_ID,
    SLACK_CLIENT_ID,
)
from shared.auth.oauth_state import mint_oauth_state

# Azure AD OAuth — tenant ID for the platform's own Azure AD directory.
# [ASSUMED] A3 — exact scope list requires verification against live Azure AD.
_AZURE_AD_TENANT_ID = "common"  # 'common' accepts any Azure AD org


def build_oauth_start_url(kind: str, tenant_id: str) -> str:
    """Build the provider OAuth authorization URL with a tenant-bound CSRF state.

    Args:
        kind: One of 'jira', 'github', 'slack', 'azure_repos'.
        tenant_id: The requesting tenant's ID (bound into CSRF state).

    Returns:
        The full authorization URL to redirect the browser to.

    Raises:
        ValueError: For unsupported connector kinds.
    """
    state = mint_oauth_state(tenant_id, kind)

    if kind == "jira":
        return _build_jira_url(state)
    if kind == "github":
        return _build_github_url(state)
    if kind == "slack":
        return _build_slack_url(state)
    if kind == "azure_repos":
        return _build_azure_repos_url(state)

    raise ValueError(f"Unknown connector kind: {kind!r}. Supported: jira, github, slack, azure_repos")


def _build_jira_url(state: str) -> str:
    """Atlassian OAuth 2.0 (3LO) authorization URL.

    Scopes: read:jira-work, write:jira-work, read:jira-user, offline_access.
    offline_access requests a refresh token (rotating, 90-day TTL).
    """
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/jira/oauth/callback"
    params = {
        "audience": "api.atlassian.com",
        "client_id": JIRA_OAUTH_CLIENT_ID,
        "scope": "read:jira-work write:jira-work read:jira-user offline_access",
        "redirect_uri": callback,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return "https://auth.atlassian.com/authorize?" + urllib.parse.urlencode(params)


def _build_github_url(state: str) -> str:
    """GitHub App user-authorization flow.

    The GitHub App (GITHUB_APP_ID) already exists; this flow associates a
    tenant with a specific GitHub App installation. Scopes are set at the
    GitHub App level (not per-OAuth-request).
    """
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/github/oauth/callback"
    params = {
        "client_id": GITHUB_APP_ID,
        "redirect_uri": callback,
        "state": state,
    }
    return "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)


def _build_slack_url(state: str) -> str:
    """Slack OAuth v2 authorization URL.

    Scopes: chat:write (send messages), channels:read (list channels).
    """
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/slack/oauth/callback"
    params = {
        "client_id": SLACK_CLIENT_ID,
        "scope": "chat:write,channels:read",
        "redirect_uri": callback,
        "state": state,
    }
    return "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(params)


def _build_azure_repos_url(state: str) -> str:
    """Azure AD OAuth 2.0 authorization URL for Azure Repos / ADO.

    [ASSUMED] A3 — exact scope list not verified against live Azure AD.
    Uses 'common' as the Azure AD tenant to support any org.
    """
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/azure_repos/oauth/callback"
    params = {
        "client_id": "common",  # replaced at registration time
        "response_type": "code",
        "redirect_uri": callback,
        "scope": "499b84ac-1321-427f-aa17-267ca6975798/.default",  # Azure DevOps scope
        "state": state,
    }
    base = f"https://login.microsoftonline.com/{_AZURE_AD_TENANT_ID}/oauth2/v2.0/authorize"
    return base + "?" + urllib.parse.urlencode(params)
