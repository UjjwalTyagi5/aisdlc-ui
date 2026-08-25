"""Connector factory — resolves the active connector at runtime (milestone-3).

Replaces provider_factory.py. Agent tools never instantiate connectors
directly; the worker calls get_connector_for_session() at task start and injects
the result via config.connectors.context.set_connector(). The factory creates
the connector with org_url only — credentials are resolved lazily inside the
connector's auth_adapter() at call time, so the factory never touches a PAT.
"""
from __future__ import annotations

import importlib

from config.connectors.base import BaseConnector, ConnectorNotAvailableError
from config.env import ADO_ORG_URL


# ── Registry: kind → connector module + class ────────────────────────────────
_CONNECTOR_REGISTRY: dict[str, str] = {
    "azure_devops":  "config.connectors.azure_devops.AzureDevOpsConnector",
    "jira":          "config.connectors.jira.JiraConnector",
    "github_issues": "config.connectors.github_issues.GitHubIssuesConnector",
    # The catalogue tile is called "github" (frontend/lib/schemas/enums.ts) while
    # the connector is named for what it drives. Without this alias every lookup
    # for the kind the UI actually stores raised ConnectorNotAvailableError.
    "github":        "config.connectors.github_issues.GitHubIssuesConnector",
    "azure_repos":   "config.connectors.azure_repos.AzureReposConnector",
    "slack":         "config.connectors.slack.SlackConnector",
    "github_actions": "config.connectors.github_actions.GitHubActionsConnector",
    "ms_teams":       "config.connectors.msteams.MSTeamsConnector",
    "sharepoint":     "config.connectors.sharepoint.SharePointConnector",
    "figma":          "config.connectors.figma.FigmaConnector",
    "confluence":     "config.connectors.confluence.ConfluenceConnector",
    "sonarqube":      "config.connectors.sonarqube.SonarQubeConnector",
}


def _load_connector_class(dotted_path: str):
    """Import and return a connector class by dotted module path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


async def _build_connector(kind: str = "azure_devops", tenant_id: str = "") -> BaseConnector:
    """Construct the raw connector for `kind`. NOT access-gated — see the public
    `get_connector_for_session` below, which is what callers must use.

    tenant_id must be a non-empty string for all connector kinds except health-probe
    calls which pass tenant_id='__health_probe__' by platform convention.
    Raises ValueError if tenant_id is empty — fail-closed per REQ-M7-03.

    The connector is created with org_url only. Credentials are fetched lazily
    inside the connector's auth_adapter() — the factory never touches them.
    """
    if tenant_id == "__health_probe__":
        pass  # Platform health probe path — no tenant context required.
    elif not tenant_id:
        raise ValueError(
            f"tenant_id is required to get a connector for kind '{kind}'. "
            "Background jobs must derive tenant_id from the entity being processed (D-04, REQ-M7-03)."
        )

    if kind not in _CONNECTOR_REGISTRY:
        raise ConnectorNotAvailableError(
            f"Unknown connector kind '{kind}'. "
            f"Supported: {', '.join(_CONNECTOR_REGISTRY)}"
        )

    connector_class = _load_connector_class(_CONNECTOR_REGISTRY[kind])

    # tenant_id is run context (not a credential): pass it so the connector's
    # auth_adapter() can resolve the tenant-scoped secret without each call site
    # threading it through. The health-probe sentinel is passed through unchanged;
    # auth_adapter then misses the tenant-scoped KV name and falls back to the
    # global name / env var, which is the intended probe credential.
    if kind == "azure_devops":
        # Prefer the per-tenant org URL set via the Integrations "Add credentials"
        # form (secret store); fall back to the env default. Skipped for the
        # health-probe sentinel and never allowed to raise.
        org_url = ADO_ORG_URL
        if tenant_id and tenant_id != "__health_probe__":
            try:
                from shared.services import secret_store
                stored = await secret_store.get_secret(tenant_id, "ado-org-url")
                if stored:
                    org_url = stored
            except Exception:
                pass
        return connector_class(org_url=org_url, tenant_id=tenant_id)
    if kind == "azure_repos":
        # Reuses ADO credentials — same org URL as AzureDevOpsConnector
        return connector_class(org_url=ADO_ORG_URL, tenant_id=tenant_id)
    if kind == "jira":
        from config.env import JIRA_URL
        # Pass tenant_id so the connector resolves THIS tenant's stored jira-url/email/
        # token (not the empty global env), mirroring azure_devops above.
        return connector_class(org_url=JIRA_URL, tenant_id=tenant_id)
    if kind == "confluence":
        from config.env import CONFLUENCE_URL
        return connector_class(org_url=CONFLUENCE_URL, tenant_id=tenant_id)
    if kind == "sonarqube":
        from config.env import SONARQUBE_URL
        return connector_class(org_url=SONARQUBE_URL, tenant_id=tenant_id)
    # slack, github_issues, github_actions, ms_teams, sharepoint, figma and any future
    # connectors: instantiate with an empty org_url; credentials are resolved lazily
    # in auth_adapter() from the tenant secret store / Key Vault.
    #
    # Every connector constructor accepts org_url= and tenant_id= precisely so this
    # tail works for all of them. SlackConnector used to take only bot_token, so this
    # call raised TypeError and get_connector_for_session(kind="slack") was unusable.
    return connector_class(org_url="", tenant_id=tenant_id)


async def list_available_connectors() -> list[dict]:
    """Return metadata for all registered connectors (used by status endpoints)."""
    result = []
    for kind, dotted_path in _CONNECTOR_REGISTRY.items():
        cls = _load_connector_class(dotted_path)
        # display_name is a read-only property; read it off a lightweight instance.
        try:
            inst = cls(org_url="")
            display_name = inst.display_name
        except Exception:
            display_name = kind
        result.append({"kind": kind, "display_name": display_name})
    return result


async def get_connector_for_session(
    kind: str = "azure_devops",
    tenant_id: str = "",
    *,
    project_id: str = "",
    owner_id: str = "",
    agent_id: str = "",
    access: str | None = None,
    unrestricted: bool = False,
) -> BaseConnector:
    """Return a connector for `kind`, bound to the access level it may use.

    THE ONE PLACE ACCESS IS ENFORCED. Agents call `read_adapter` / `write_adapter`
    from roughly twenty sites across eight modules, and every new agent adds more. A
    permission check written at those sites is one somebody will forget, and the one
    they forget is the one that matters. Every connector is obtained here, so the
    level is bound here and every existing call site became governed without being
    edited — see `config/connectors/scoped.ScopedConnector`.

    Pass exactly one of:

      project_id    resolve the effective level for a stage. The agent runtime path:
                    it has `SDLCWorkflowInput.project_id` and no session. Pass
                    `agent_id` WITH it — since migration 0024 the level is stored per
                    (stage, tool), so a project_id with no stage names no level and
                    resolves to no access. That is the safe direction, but it will
                    look like a broken connector, so it is called out here.
      access        a level the caller already resolved. The request path, which has a
                    session open and should not open a second one.
      unrestricted  no gating. Health probes and org-level admin operations that act
                    for no project. Explicit and named, because it is the fail-open
                    door and it should read as one at the call site.

    `owner_id`, alongside `project_id`, is who the run belongs to — not an access
    decision, a credential one. It lets `BaseConnector._resolve_credential_override`
    (config/connectors/base.py) check whether THIS person saved their own credential
    for this connector on this project (`project_integration_credentials`) before the
    connector falls back to the tenant-wide one. Omitted, connector calls behave
    exactly as before this existed — the tenant-wide credential, unconditionally.

    Passing none of them yields a connector that permits NOTHING — a caller who has
    not established what they may do has not established that they may do anything.
    The failure is then a clear `ConnectorAccessDenied` at first use rather than a
    silent full grant.
    """
    raw = await _build_connector(kind=kind, tenant_id=tenant_id)
    # Routing identifiers, not secrets — see the "not a REQ-M3-10 violation" note on
    # BaseConnector._resolve_credential_override.
    raw._project_id_for_creds = project_id  # noqa: SLF001
    raw._project_owner_id = owner_id  # noqa: SLF001

    if unrestricted:
        return raw

    level = access
    if level is None and project_id:
        from shared.authz.connector_grants import resolve_effective_access

        level = await resolve_effective_access(
            tenant_id=tenant_id, project_id=project_id, target_ref=kind,
            kind="connector", agent_id=agent_id,
        )

    from config.connectors.scoped import ScopedConnector

    return ScopedConnector(raw, level)
