import { z } from "zod";

import { Connector, type ConnectorKind } from "@/lib/schemas";
import { ConnectorGrant, SetCredentialsResult } from "@/lib/schemas/connector";

import { api } from "./client";

/**
 * Which connector kinds the Organization Admin permits.
 *
 * Omit `workspaceId` to read the whole policy (Org Admin only). Pass one to
 * read what reaches that Business Unit — the form a BU or Project Admin needs,
 * and the only form they are allowed.
 */
export const listConnectorGrants = (workspaceId?: string | null) =>
  api("/connectors/grants", {
    query: { workspaceId: workspaceId || undefined },
    schema: z.array(ConnectorGrant),
  });

/** Replace the whole policy — the per-connector visibility control. */
export const setConnectorGrants = (grants: ConnectorGrant[]) =>
  api("/connectors/grants", { method: "PUT", body: { grants }, schema: z.array(ConnectorGrant) });

/** Grant one Business Unit a set of kinds, from its creation / management. */
export const setBuConnectorGrants = (workspaceId: string, kinds: ConnectorKind[]) =>
  api("/connectors/grants", {
    method: "PUT",
    query: { workspaceId },
    body: { kinds },
    schema: z.array(ConnectorGrant),
  });

/**
 * Connectors visible to a Business Unit: org-wide ones plus that unit's own
 * (PRD §34.3). Omit `workspaceId` only where there genuinely is no unit
 * context — passing one is what keeps a unit from seeing a sibling's
 * integrations.
 */
export const listConnectors = (workspaceId?: string | null) =>
  api("/connectors", {
    query: { workspaceId: workspaceId || undefined },
    schema: z.array(Connector),
  });

export const getConnector = (kind: ConnectorKind) =>
  api(`/connectors/${encodeURIComponent(kind)}`, { schema: Connector });

export const disconnectConnector = (kind: ConnectorKind) =>
  api(`/connectors/${encodeURIComponent(kind)}/disconnect`, {
    method: "POST",
    schema: Connector,
  });

/**
 * Store pasted credentials for a connector and verify them with a live probe.
 * Azure DevOps: `{ org_url, pat }`. Jira: `{ base_url, email, api_token }`.
 * The secret is sent once and never returned.
 */
export const setConnectorCredentials = (
  kind: ConnectorKind,
  body: {
    org_url?: string;
    pat?: string;
    base_url?: string;
    email?: string;
    api_token?: string;
    owner?: string;
    /** Confluence default space (optional convenience — not required to connect). */
    space_key?: string;
    /** GitHub App the TENANT registered in its own org. The platform no longer
     *  registers one on everybody's behalf, so these come from the tenant. */
    github_app_id?: string;
    github_app_private_key?: string;
    github_app_installation_id?: string;
    /** INBOUND webhook signing secret, per tenant — the value set when creating the
     *  webhook in the tenant's own GitHub org / Jira site / Slack app. Optional: a
     *  tenant that receives no webhooks has nothing to set. Azure DevOps service
     *  hooks use HTTP Basic instead of an HMAC, so they also need webhook_user. */
    webhook_secret?: string;
    webhook_user?: string;
    /** Which Business Unit the resulting connection belongs to. Required of a
     *  viewer bound to more than one — a credential lands in exactly one unit,
     *  and picking for them is how a key ends up in the wrong one. Ignored for
     *  an Org Admin, whose connections are org-wide. */
    workspaceId?: string | null;
  },
) =>
  api(`/connectors/${encodeURIComponent(kind)}/credentials`, {
    method: "POST",
    body,
    schema: SetCredentialsResult,
  });

// REMOVED: installConnector() and completeOAuthCallback(). Connecting a provider is
// now one step — setConnectorCredentials() above — because the OAuth flow they drove
// required the platform to register and hold an OAuth app on every tenant's behalf.
