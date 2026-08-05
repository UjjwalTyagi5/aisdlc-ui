import type { ConnectorKind } from "@/lib/schemas/enums";

/**
 * Display names for connector kinds.
 *
 * Lifted out of the Integrations page once the Business Unit management screen
 * started naming connectors too: two hand-maintained maps of the same eight
 * strings is one rename away from the same integration having two names in the
 * same product.
 */
export const CONNECTOR_KIND_LABEL: Record<ConnectorKind, string> = {
  jira: "Jira",
  azure_devops: "Azure DevOps",
  github: "GitHub",
  azure_repos: "Azure Repos",
  github_actions: "GitHub Actions",
  slack: "Slack",
  ms_teams: "Microsoft Teams",
  sharepoint: "SharePoint",
  sso_okta: "Okta SSO",
  sso_entra: "Microsoft Entra SSO",
};

export function connectorKindLabel(kind: string): string {
  return CONNECTOR_KIND_LABEL[kind as ConnectorKind] ?? kind;
}
