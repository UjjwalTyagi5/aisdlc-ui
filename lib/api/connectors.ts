import { z } from "zod";

import { Connector, ConnectorInstallResponse, type ConnectorKind } from "@/lib/schemas";
import { SetCredentialsResult } from "@/lib/schemas/connector";

import { api } from "./client";

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

/**
 * Wave C — POST /api/connectors/{kind}/install
 *
 * Returns a union: either `{redirectUrl: string}` (OAuth redirect path) or a
 * full `Connector` record (non-OAuth / backward-compat path). The caller's
 * `onSuccess` handler checks `data.redirectUrl` and calls
 * `window.location.assign(data.redirectUrl)` when present (UI-SPEC step 2).
 */
export const installConnector = (kind: ConnectorKind) =>
  api(`/connectors/${encodeURIComponent(kind)}/install`, {
    method: "POST",
    schema: ConnectorInstallResponse,
  });

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
  },
) =>
  api(`/connectors/${encodeURIComponent(kind)}/credentials`, {
    method: "POST",
    body,
    schema: SetCredentialsResult,
  });

/**
 * Wave C — complete the OAuth callback flow (Connect Flow steps 4-5).
 *
 * Called by the OAuthCallbackHandler page on mount. Forwards `code` and
 * `state` to the BFF callback route which validates CSRF state server-side
 * (FastAPI verify_oauth_state) and exchanges the code for tokens.
 *
 * Returns the resulting `Connector` record with `installed: true`.
 */
export const completeOAuthCallback = (kind: string, code: string, state: string) =>
  api(
    `/connectors/${encodeURIComponent(kind)}/oauth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`,
    { method: "GET", schema: Connector },
  );
