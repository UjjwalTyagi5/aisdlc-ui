import { z } from "zod";

import { McpServer, McpTestResult, type McpTransport } from "@/lib/schemas/mcp";

import { api } from "./client";

export interface McpServerInput {
  server_name: string;
  description?: string | null;
  transport: McpTransport;
  url?: string | null;
  command?: string | null;
  args?: string[];
  env_vars?: Record<string, string> | null;
  headers?: Record<string, string> | null;
  is_active?: boolean;
}

export type McpServerUpdate = Partial<McpServerInput>;

/**
 * Omit `workspaceId` for the registry admin page's own view (servers YOU
 * registered). Pass one to ask what a Business Unit was GRANTED instead,
 * regardless of who registered it — the Tools-per-stage picker's question.
 */
export const listMcpServers = (activeOnly = false, workspaceId?: string | null) =>
  api("/mcp/registry", {
    query: { active_only: activeOnly, workspaceId: workspaceId || undefined },
    schema: z.array(McpServer),
  });

export const getMcpServer = (id: string) =>
  api(`/mcp/registry/${encodeURIComponent(id)}`, { schema: McpServer });

export const createMcpServer = (body: McpServerInput) =>
  api("/mcp/registry", { method: "POST", body, schema: McpServer });

export const updateMcpServer = (id: string, body: McpServerUpdate) =>
  api(`/mcp/registry/${encodeURIComponent(id)}`, { method: "PUT", body, schema: McpServer });

export const deleteMcpServer = (id: string) =>
  api(`/mcp/registry/${encodeURIComponent(id)}`, { method: "DELETE" });

export const testMcpConnection = (body: Omit<McpServerInput, "is_active" | "allowed_stages">) =>
  api("/mcp/registry/test-connection", { method: "POST", body, schema: McpTestResult });

export const probeMcpServer = (id: string) =>
  api(`/mcp/registry/${encodeURIComponent(id)}/probe`, { method: "POST", schema: McpTestResult });
