import type { ConnectorHealth, ConnectorKind } from "@/lib/schemas/enums";

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
  figma: "Figma",
  sso_okta: "Okta SSO",
  sso_entra: "Microsoft Entra SSO",
};

export function connectorKindLabel(kind: string): string {
  return CONNECTOR_KIND_LABEL[kind as ConnectorKind] ?? kind;
}

/**
 * User-facing status word for a connector's health.
 *
 * The backend still tracks three states (`healthy` / `degraded` /
 * `disconnected`) so alerts and diagnostics can tell "flaky" apart from
 * "down". But the UI collapses that to the two words a user actually acts
 * on: only a fully healthy connector reads as "Connected" — anything less
 * reads as "Disconnected", since degraded is still a problem to fix.
 */
export function connectorStatusLabel(health: ConnectorHealth): "Connected" | "Disconnected" {
  return health === "healthy" ? "Connected" : "Disconnected";
}
