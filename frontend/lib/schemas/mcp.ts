import { z } from "zod";

export const McpTransport = z.enum(["streamable_http", "sse", "stdio"]);
export type McpTransport = z.infer<typeof McpTransport>;

/**
 * Which Business Units may use an MCP server — the MCP twin of
 * `ConnectorGrant`, and it drops `visibility` for the same reason.
 *
 * Keyed by server id rather than by kind, which is the one real difference
 * from connectors: a connector kind is a vendor with at most one connection
 * per tenant, while every MCP server is its own instance. There is no
 * catalogue of "MCP kinds" to grant.
 *
 * The grant is the WHOLE answer. A server used to carry its own scope, pinning
 * it to the unit that registered it — that mattered only while a unit held its
 * own credential for it, which nobody does now.
 */
export const McpServerGrant = z.object({
  serverId: z.string(),
  businessUnitIds: z.array(z.string()).default([]),
});
export type McpServerGrant = z.infer<typeof McpServerGrant>;

export const McpToolInfo = z.object({
  name: z.string(),
  description: z.string().optional().default(""),
});
export type McpToolInfo = z.infer<typeof McpToolInfo>;

/** A registered MCP server as returned by the API (never includes secret values). */
export const McpServer = z.object({
  id: z.string(),
  server_name: z.string(),
  description: z.string().nullable().optional(),
  transport: McpTransport,
  url: z.string().nullable().optional(),
  command: z.string().nullable().optional(),
  args: z.array(z.string()).optional().default([]),
  has_env_vars: z.boolean().default(false),
  has_headers: z.boolean().default(false),
  is_active: z.boolean().default(true),
  allowed_stages: z.array(z.string()).nullable().optional(),
  tools_snapshot: z.array(McpToolInfo).optional().default([]),
  created_by: z.string().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type McpServer = z.infer<typeof McpServer>;

export const McpTestResult = z.object({
  ok: z.boolean(),
  tools: z.array(McpToolInfo).default([]),
  error: z.string().nullable().optional(),
});
export type McpTestResult = z.infer<typeof McpTestResult>;
