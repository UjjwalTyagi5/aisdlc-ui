/**
 * Dummy MCP server registry — plain data, server-safe (imported by the
 * app/api/mcp/registry route handler). This is the DUMMY-DATA source; a real
 * MCP registry service replaces the route-handler body, not these shapes.
 */
import type { McpServer } from "@/lib/schemas/mcp";

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
];

export function listMcpServers(activeOnly?: boolean): McpServer[] {
  return activeOnly ? MCP_SERVERS.filter((s) => s.is_active) : MCP_SERVERS;
}

export function getMcpServer(id: string): McpServer | undefined {
  return MCP_SERVERS.find((s) => s.id === id);
}
