/**
 * What a project may use, and the credentials it holds against those things.
 *
 * This is the consumption end of the cascade. The org grants a kind to a unit,
 * the unit's projects are wired to specific integrations, and THIS is where
 * that becomes something a team can actually run — because a project's
 * identity against a tool is its own, not the onboarding admin's.
 *
 * Plain data + functions, server-safe, shared by the Next route handlers and
 * mocks/handlers.ts ([[msw-dual-runtime-mutation-rule]]).
 */
import { PROJECTS, CONNECTORS } from "@/mocks/fixtures";
import { MCP_SERVERS, mcpServersForWorkspace } from "@/lib/mock/mcp-fixtures";
import { permittedConnectorKinds } from "@/lib/mock/connector-grants";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import type {
  ProjectIntegration,
  ProjectIntegrationCredential,
  ProjectIntegrationCredentialInput,
} from "@/lib/schemas/project-integration";

/**
 * Kinds whose access is per-team rather than per-tenant: a shared bot token
 * authenticates the ORGANIZATION, and these authenticate a project inside it.
 *
 * The list is deliberately short. Asking every project for a credential
 * against every tool would turn a governance win into onboarding friction, so
 * only the tools where a per-team identity is real (a repo bot, a board
 * account, a database role) ask for one.
 */
const PROJECT_CREDENTIAL_KINDS = new Set(["jira", "github", "azure_devops", "github_actions"]);

let CREDENTIALS: ProjectIntegrationCredential[] = [
  {
    id: "pic_1",
    projectId: "payments-api",
    kind: "connector",
    targetId: "jira",
    label: "Payments delivery bot",
    account: "svc-payments@acme.test",
    hasSecret: true,
    updatedBy: "Ada Lovelace",
    updatedAt: "2026-07-02T10:15:00.000Z",
  },
  {
    id: "pic_2",
    projectId: "mobile-onboarding",
    kind: "connector",
    targetId: "github",
    label: "Onboarding CI bot",
    account: "acme-onboarding-ci",
    hasSecret: true,
    updatedBy: "Grace Hopper",
    updatedAt: "2026-07-18T08:40:00.000Z",
  },
];

export function listProjectCredentials(projectId: string): ProjectIntegrationCredential[] {
  return CREDENTIALS.filter((c) => c.projectId === projectId).map((c) => ({ ...c }));
}

/**
 * Create or replace the project's credential for one integration.
 *
 * One credential per (project, integration): a second identity against the
 * same tool has nothing to distinguish it in any UI the project has, and the
 * common case for "another key" is rotation, which is a replacement. Omitting
 * `secret` on an update keeps the stored one — the form leaves the field blank
 * when it is only relabelling, and a blank field must not silently clear a
 * working credential.
 */
export function upsertProjectCredential(
  projectId: string,
  input: ProjectIntegrationCredentialInput,
  updatedBy: string,
): ProjectIntegrationCredential {
  const existing = CREDENTIALS.find(
    (c) => c.projectId === projectId && c.kind === input.kind && c.targetId === input.targetId,
  );
  const now = new Date().toISOString();

  if (existing) {
    existing.label = input.label;
    existing.account = input.account ?? null;
    if (input.secret) existing.hasSecret = true;
    existing.updatedBy = updatedBy;
    existing.updatedAt = now;
    return { ...existing };
  }

  const created: ProjectIntegrationCredential = {
    id: `pic_${Date.now().toString(36)}`,
    projectId,
    kind: input.kind,
    targetId: input.targetId,
    label: input.label,
    account: input.account ?? null,
    hasSecret: Boolean(input.secret),
    updatedBy,
    updatedAt: now,
  };
  CREDENTIALS.push(created);
  return { ...created };
}

export function removeProjectCredential(projectId: string, id: string): boolean {
  const before = CREDENTIALS.length;
  CREDENTIALS = CREDENTIALS.filter((c) => !(c.id === id && c.projectId === projectId));
  return CREDENTIALS.length < before;
}

/**
 * The integrations approved for one project, with its credentials attached.
 *
 * Approval is the INTERSECTION of two decisions, and both have to hold: the
 * project was wired to the integration (`project.connectors` / `mcpServers`,
 * set by its Project Admin), and the integration still reaches the project's
 * Business Unit. A grant revoked upstream therefore removes the row rather
 * than leaving a tool the project can see and cannot call — which is the
 * failure the whole cascade exists to prevent.
 */
export function listProjectIntegrations(projectId: string): ProjectIntegration[] {
  const project = PROJECTS.find((p) => String(p.id) === projectId);
  if (!project) return [];
  const workspaceId = project.workspaceId ?? null;
  const credentials = listProjectCredentials(projectId);
  const out: ProjectIntegration[] = [];

  // ── Connectors, by kind, with the stages they are wired to ──
  const permitted = workspaceId ? new Set<string>(permittedConnectorKinds(workspaceId)) : null;
  const stagesByKind = new Map<string, string[]>();
  for (const [stage, kinds] of Object.entries(project.connectors ?? {})) {
    for (const kind of kinds ?? []) {
      stagesByKind.set(kind, [...(stagesByKind.get(kind) ?? []), stage]);
    }
  }

  for (const [kind, stages] of stagesByKind) {
    if (permitted && !permitted.has(kind)) continue;
    const connection = CONNECTORS.find((c) => c.kind === kind);

    out.push({
      kind: "connector",
      id: kind,
      name: connection?.name ?? CONNECTOR_KIND_LABEL[kind as keyof typeof CONNECTOR_KIND_LABEL] ?? kind,
      description: null,
      origin: "organization",
      stages,
      needsProjectCredential: PROJECT_CREDENTIAL_KINDS.has(kind),
      credential:
        credentials.find((c) => c.kind === "connector" && c.targetId === kind) ?? null,
    });
  }

  // ── MCP servers, by id ──
  const reachable = workspaceId
    ? new Set(mcpServersForWorkspace(workspaceId).map((s) => s.id))
    : null;
  const assigned = new Set<string>();
  for (const ids of Object.values(project.mcpServers ?? {})) {
    for (const id of ids ?? []) assigned.add(id);
  }

  for (const id of assigned) {
    if (reachable && !reachable.has(id)) continue;
    const server = MCP_SERVERS.find((s) => s.id === id);
    if (!server) continue;
    out.push({
      kind: "mcp",
      id,
      name: server.server_name,
      description: server.description ?? null,
      // Every integration is org-level now; a unit holds it by grant, not by
      // owning it. The field survives for connectors written before that.
      origin: "organization",
      stages: [],
      // A server declaring env vars or headers is one expecting per-caller
      // configuration; one that declares neither runs on the connection alone.
      needsProjectCredential: server.has_env_vars || server.has_headers,
      credential: credentials.find((c) => c.kind === "mcp" && c.targetId === id) ?? null,
    });
  }

  return out;
}
