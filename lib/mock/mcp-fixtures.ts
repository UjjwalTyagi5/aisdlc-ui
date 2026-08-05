/**
 * Dummy MCP server registry — plain data, server-safe (imported by the
 * app/api/mcp/registry route handler). This is the DUMMY-DATA source; a real
 * MCP registry service replaces the route-handler body, not these shapes.
 */
import type { McpServer, McpServerGrant } from "@/lib/schemas/mcp";

export const MCP_SERVERS: McpServer[] = [
  {
    id: "mcp_filesystem",
    server_name: "Filesystem",
    description: "Read/write access to the project's repo checkout.",
    transport: "stdio",
    url: null,
    command: "mcp-server-filesystem",
    args: [],
    has_env_vars: false,
    has_headers: false,
    is_active: true,
    allowed_stages: null,
    tools_snapshot: [
      { name: "read_file", description: "Read a file's contents" },
      { name: "write_file", description: "Write or overwrite a file" },
      { name: "list_directory", description: "List a directory's entries" },
    ],
    created_by: "org_admin",
    created_at: "2026-02-01T09:00:00.000Z",
    updated_at: null,
  },
  {
    id: "mcp_postgres",
    server_name: "Postgres (staging)",
    description: "Query access to the staging database for data verification.",
    transport: "streamable_http",
    url: "https://mcp-postgres.internal.acme.test",
    command: null,
    args: [],
    has_env_vars: true,
    has_headers: false,
    is_active: true,
    allowed_stages: null,
    tools_snapshot: [
      { name: "query", description: "Run a read-only SQL query" },
      { name: "describe_table", description: "Get a table's schema" },
    ],
    created_by: "org_admin",
    created_at: "2026-02-10T09:00:00.000Z",
    updated_at: null,
  },
  {
    id: "mcp_web_search",
    server_name: "Web Search",
    description: "General web search for research and citations.",
    transport: "sse",
    url: "https://mcp-search.internal.acme.test",
    command: null,
    args: [],
    has_env_vars: true,
    has_headers: false,
    is_active: true,
    allowed_stages: null,
    tools_snapshot: [{ name: "search", description: "Search the web" }],
    created_by: "org_admin",
    created_at: "2026-03-01T09:00:00.000Z",
    updated_at: null,
  },
  // Granted to Payments alone — the narrow case, so the access screen has a
  // server that does not reach everyone ([[scoped-fixtures-need-coverage]]).
  {
    id: "mcp_card_scheme",
    server_name: "Card scheme sandbox",
    description: "Visa/Mastercard test harness. Payments' own contract.",
    transport: "streamable_http",
    url: "https://mcp-scheme.payments.acme.test",
    command: null,
    args: [],
    has_env_vars: true,
    has_headers: true,
    is_active: true,
    allowed_stages: null,
    tools_snapshot: [
      { name: "authorize", description: "Run a test authorization" },
      { name: "settle", description: "Settle a test authorization" },
    ],
    created_by: "bu_admin",
    created_at: "2026-04-02T09:00:00.000Z",
    updated_at: null,
  },
];

/**
 * Which units each org-wide server reaches. Mirrors `CONNECTOR_GRANTS`, and
 * seeded the same way: both visibilities carry real data, because a grant UI
 * that can only ever render "global" never shows what a selection looks like.
 *
 * Postgres (staging) is deliberately narrow — a staging database is exactly
 * the kind of thing an organization hands to named units rather than to all.
 */
let MCP_GRANTS: McpServerGrant[] = [
  { serverId: "mcp_filesystem", businessUnitIds: ["ws_lending", "ws_payments", "ws_platform"] },
  { serverId: "mcp_web_search", businessUnitIds: ["ws_lending", "ws_payments", "ws_platform"] },
  { serverId: "mcp_postgres", businessUnitIds: ["ws_payments", "ws_lending"] },
  // Needs an explicit grant like everything else. It used to reach Payments
  // through its `scope` alone, which stopped bounding anything when the
  // credential model went — leaving it granted to nobody.
  { serverId: "mcp_card_scheme", businessUnitIds: ["ws_payments"] },
];

export function listMcpGrants(): McpServerGrant[] {
  return MCP_GRANTS.map((g) => ({ ...g, businessUnitIds: [...g.businessUnitIds] }));
}

/** Replace the whole list. A server granted to no unit is dropped: "granted to
 *  nobody" and "not granted" are the same state. */
export function setMcpGrants(grants: McpServerGrant[]): McpServerGrant[] {
  const seen = new Set<string>();
  MCP_GRANTS = grants.flatMap((g) => {
    if (seen.has(g.serverId)) return [];
    seen.add(g.serverId);
    const units = [...new Set(g.businessUnitIds)];
    return units.length > 0 ? [{ serverId: g.serverId, businessUnitIds: units }] : [];
  });
  return listMcpGrants();
}

/** Grant one unit exactly `serverIds` — the BU-creation and matrix control. */
export function setBuMcpGrants(workspaceId: string, serverIds: string[]): McpServerGrant[] {
  const wanted = new Set(serverIds);
  for (const g of MCP_GRANTS) {
    const has = g.businessUnitIds.includes(workspaceId);
    const want = wanted.has(g.serverId);
    if (want && !has) g.businessUnitIds.push(workspaceId);
    else if (!want && has) {
      g.businessUnitIds = g.businessUnitIds.filter((id) => id !== workspaceId);
    }
  }
  for (const id of wanted) {
    if (!MCP_GRANTS.some((g) => g.serverId === id)) {
      MCP_GRANTS.push({ serverId: id, businessUnitIds: [workspaceId] });
    }
  }
  MCP_GRANTS = MCP_GRANTS.filter((g) => g.businessUnitIds.length > 0);
  return MCP_GRANTS.filter((g) => g.businessUnitIds.includes(workspaceId));
}

/** Give ONE unit access to one server. Creates the grant if it has none. */
export function grantMcpToUnit(serverId: string, workspaceId: string): string[] {
  let grant = MCP_GRANTS.find((g) => g.serverId === serverId);
  if (!grant) {
    grant = { serverId, businessUnitIds: [] };
    MCP_GRANTS.push(grant);
  }
  if (!grant.businessUnitIds.includes(String(workspaceId))) {
    grant.businessUnitIds.push(String(workspaceId));
  }
  return [...grant.businessUnitIds];
}

/** Revoke ONE unit's access to one server. Returns the surviving units. */
export function revokeMcpGrant(serverId: string, workspaceId: string): string[] {
  const grant = MCP_GRANTS.find((g) => g.serverId === serverId);
  if (!grant) return [];
  grant.businessUnitIds = grant.businessUnitIds.filter((id) => id !== String(workspaceId));
  const left = [...grant.businessUnitIds];
  MCP_GRANTS = MCP_GRANTS.filter((g) => g.businessUnitIds.length > 0);
  return left;
}

/**
 * The servers one Business Unit may use: the org-wide ones granted to it, plus
 * the ones onboarded into the unit itself.
 *
 * An org-wide server with NO grant record reaches nobody. That is the safe
 * reading rather than the convenient one — a server registered but never
 * granted is a server nobody decided to hand out, and defaulting it to global
 * would make the grant list decorative.
 */
export function mcpServersForWorkspace(workspaceId: string): McpServer[] {
  // The grant is the whole answer. A server's own `scope` used to pin it to
  // the unit that registered it, which mattered while a unit held its own
  // credential — nothing does now.
  const grants = listMcpGrants();
  return MCP_SERVERS.filter((s) => {
    const grant = grants.find((g) => g.serverId === s.id);
    return grant ? grant.businessUnitIds.includes(String(workspaceId)) : false;
  });
}

/** The union over several units, deduped — the answer for a viewer bound to
 *  more than one ([[no-bu-switcher-in-chrome]]). */
export function mcpServersForWorkspaces(workspaceIds: string[]): McpServer[] {
  const seen = new Set<string>();
  return workspaceIds.flatMap((id) =>
    mcpServersForWorkspace(id).filter((s) => {
      if (seen.has(s.id)) return false;
      seen.add(s.id);
      return true;
    }),
  );
}

export function listMcpServers(activeOnly?: boolean): McpServer[] {
  return activeOnly ? MCP_SERVERS.filter((s) => s.is_active) : MCP_SERVERS;
}

/**
 * The registry as one viewer may see it ([[access-scope-rbac-layer]]).
 *
 * `allowedWorkspaceIds: null` is the org-wide viewer — they see everything,
 * including servers a unit registered itself, because oversight of the estate
 * is the job left to them once they stopped consuming it. A bounded viewer
 * sees the union over their own units and nothing from a sibling's.
 */
export function listMcpServersForScope(
  allowedWorkspaceIds: string[] | null,
  activeOnly?: boolean,
): McpServer[] {
  const all = listMcpServers(activeOnly);
  if (allowedWorkspaceIds === null) return all;
  const visible = new Set(mcpServersForWorkspaces(allowedWorkspaceIds).map((s) => s.id));
  return all.filter((s) => visible.has(s.id));
}

/**
 * Register a server. It reaches nobody until it is granted — deliberately: a
 * server that appeared in every unit's catalogue the moment it was registered
 * would make the grant step optional, and the grant step is the whole control.
 */
export function createMcpServer(
  input: Partial<McpServer> & { server_name: string },
): McpServer {
  const created: McpServer = {
    id: `mcp_${Date.now().toString(36)}`,
    server_name: input.server_name,
    description: input.description ?? null,
    transport: input.transport ?? "streamable_http",
    url: input.url ?? null,
    command: input.command ?? null,
    args: input.args ?? [],
    has_env_vars: Boolean(input.has_env_vars),
    has_headers: Boolean(input.has_headers),
    is_active: input.is_active ?? true,
    allowed_stages: input.allowed_stages ?? null,
    tools_snapshot: input.tools_snapshot ?? [],
    created_by: "org_admin",
    created_at: new Date().toISOString(),
    updated_at: null,
  };
  MCP_SERVERS.push(created);
  return created;
}

export function getMcpServer(id: string): McpServer | undefined {
  return MCP_SERVERS.find((s) => s.id === id);
}
