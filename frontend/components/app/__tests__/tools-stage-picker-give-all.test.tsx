// @vitest-environment jsdom
/**
 * "Give all" on the per-stage tool picker.
 *
 * Wiring seven tools to eight stages was fifty-six clicks in a popover that closes
 * between them, on the dialog every project starts from. One click per stage gives that
 * agent every tool; one click at the top does every stage. Everything given keeps the
 * default access mode (read & write), exactly as ticking each tool by hand would, and
 * clearing a stage drops the modes that were set on its tools.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/connectors", () => ({
  listConnectors: async () => [
    { kind: "jira", granted: true, agentToolsAvailable: true },
    { kind: "confluence", granted: true, agentToolsAvailable: true },
    { kind: "slack", granted: false, agentToolsAvailable: true },
  ],
}));
vi.mock("@/lib/api/mcp", () => ({
  listMcpServers: async () => [{ id: "srv-1", server_name: "Docs MCP", transport: "sse" }],
}));
vi.mock("@/lib/connectors", () => ({ connectorKindLabel: (k: string) => k[0]!.toUpperCase() + k.slice(1) }));

import { TooltipProvider } from "@/components/ui/tooltip";
import { ToolsStagePicker } from "@/components/app/tools-stage-picker";

afterEach(cleanup);

function renderPicker(over: Partial<React.ComponentProps<typeof ToolsStagePicker>> = {}) {
  const onConnectorChange = vi.fn();
  const onMcpChange = vi.fn();
  const onAccessModeChange = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
      <ToolsStagePicker
        mcpValue={{}}
        connectorValue={{}}
        accessModeValue={{}}
        onConnectorChange={onConnectorChange}
        onMcpChange={onMcpChange}
        onAccessModeChange={onAccessModeChange}
        workspaceId="bu-1"
        track="greenfield"
        {...over}
      />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { onConnectorChange, onMcpChange, onAccessModeChange };
}

describe("Give all", () => {
  it("gives one stage every granted connector and every MCP server", async () => {
    const { onConnectorChange, onMcpChange } = renderPicker();
    fireEvent.click(await screen.findByRole("button", { name: "Give all tools to Requirements" }));
    expect(onConnectorChange).toHaveBeenCalledWith({ requirements: ["jira", "confluence"] });
    expect(onMcpChange).toHaveBeenCalledWith({ requirements: ["srv-1"] });
  });

  it("does not disturb the other stages' choices", async () => {
    const { onConnectorChange } = renderPicker({ connectorValue: { design: ["jira"] } });
    fireEvent.click(await screen.findByRole("button", { name: "Give all tools to Requirements" }));
    expect(onConnectorChange).toHaveBeenCalledWith({ design: ["jira"], requirements: ["jira", "confluence"] });
  });

  it("offers Clear all once a stage has everything, and clearing drops its access modes", async () => {
    const { onConnectorChange, onMcpChange, onAccessModeChange } = renderPicker({
      connectorValue: { requirements: ["jira", "confluence"] },
      mcpValue: { requirements: ["srv-1"] },
      accessModeValue: { "requirements::connector::jira": "read", "design::connector::jira": "write" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Clear all tools for Requirements" }));
    expect(onConnectorChange).toHaveBeenCalledWith({ requirements: [] });
    expect(onMcpChange).toHaveBeenCalledWith({ requirements: [] });
    expect(onAccessModeChange).toHaveBeenCalledWith({ "design::connector::jira": "write" });
  });

  it("gives every stage on the track all tools in one click", async () => {
    const { onConnectorChange, onMcpChange } = renderPicker();
    fireEvent.click(await screen.findByRole("button", { name: /Give every stage all tools/ }));
    const conn = onConnectorChange.mock.calls[0]![0] as Record<string, string[]>;
    const mcp = onMcpChange.mock.calls[0]![0] as Record<string, string[]>;
    expect(Object.keys(conn).length).toBeGreaterThan(5);
    expect(Object.values(conn).every((ids) => ids.join() === "jira,confluence")).toBe(true);
    expect(Object.values(mcp).every((ids) => ids.join() === "srv-1")).toBe(true);
    expect(conn.code_review).toEqual(["jira", "confluence"]);
  });
});
