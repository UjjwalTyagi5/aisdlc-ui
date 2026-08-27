// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AgentRail } from "../agent-rail";
import type { AgentProfileSummaryEntry } from "@/lib/schemas/agent-profiles";

afterEach(cleanup);

function entry(overrides: Partial<AgentProfileSummaryEntry>): AgentProfileSummaryEntry {
  return {
    agent_id: "design",
    active_version: null,
    latest_version: null,
    draft_count: 0,
    updated_at: null,
    active: null,
    inherited_from: null,
    ...overrides,
  };
}

describe("AgentRail inheritance badge", () => {
  it("shows an own-tier version badge when active_version is set, no inherited badge", () => {
    render(
      <AgentRail
        agents={[entry({ active_version: 3, latest_version: 3 })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("v3")).toBeTruthy();
    expect(screen.queryByText(/Inherited/)).toBeNull();
  });

  it("shows 'Inherited from Org' when inherited_from is org and nothing is active locally", () => {
    render(
      <AgentRail
        agents={[entry({ inherited_from: "org" })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Inherited from Org")).toBeTruthy();
  });

  it("shows 'Inherited from Business Unit' for workspace", () => {
    render(
      <AgentRail
        agents={[entry({ inherited_from: "workspace" })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Inherited from Business Unit")).toBeTruthy();
  });

  it("shows 'Inherited from Project' for project", () => {
    render(
      <AgentRail
        agents={[entry({ inherited_from: "project" })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Inherited from Project")).toBeTruthy();
  });

  it("falls back to a plain 'default' label when there's no active version and nothing inherited", () => {
    render(
      <AgentRail
        agents={[entry({})]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("default")).toBeTruthy();
  });
});
