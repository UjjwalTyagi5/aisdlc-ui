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
  azure_pipelines: "Azure Pipelines",
  github_actions: "GitHub Actions",
  slack: "Slack",
  ms_teams: "Microsoft Teams",
  sharepoint: "SharePoint",
  figma: "Figma",
  confluence: "Confluence",
  sonarqube: "SonarQube",
  sso_okta: "Okta SSO",
  sso_entra: "Microsoft Entra SSO",
};

export function connectorKindLabel(kind: string): string {
  return CONNECTOR_KIND_LABEL[kind as ConnectorKind] ?? kind;
}

/**
 * The connector kinds the product PRESENTS — one tile each on the Integrations
 * page, and the "N available" any count must be measured against.
 *
 * Narrower than `ConnectorKind`, which is the set the API accepts: `azure_repos`
 * and `azure_pipelines` are folded into the consolidated Azure DevOps tile (one
 * credential covers boards, repos and CI/CD), and the two SSO kinds are identity
 * plumbing rather than something a project connects.
 *
 * Anything that counts connectors reads this, because the alternative is
 * counting whatever a given source happens to hold — which is how the dashboard
 * came to claim eleven while the page it links to showed eight. Mirrors
 * `_CATALOG_KINDS` in backend/shared/routers/connectors.py.
 */
export const CONNECTOR_CATALOG_KINDS: ConnectorKind[] = [
  "jira",
  "azure_devops",
  "github",
  "github_actions",
  "slack",
  "ms_teams",
  "sharepoint",
  "figma",
  "confluence",
  "sonarqube",
];

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
