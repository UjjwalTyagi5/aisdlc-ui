// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";

/**
 * The tab name is a PROP rather than a rename. It was shared with the Copilot until
 * Phase 5 deleted that surface, and the distinction it encodes outlives it: what the
 * STANDALONE agents write really is an Artifact, approval-gated and in its own table,
 * while the Orchestrator's output is a different concept and says so.
 */

vi.mock("@/lib/api/runs", () => ({ getRun: async () => ({ id: "run-1" }) }));
vi.mock("@/components/orchestrator/artifact-viewer", () => ({
  ArtifactViewer: () => <div data-testid="viewer" />,
}));

// jsdom has no ResizeObserver, and the panel observes its list/viewer split. Without
// this the list never renders and every assertion below fails on a missing element
// rather than on the thing it is testing.
class _ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", _ResizeObserver);

afterEach(cleanup);

const base = {
  runId: "run-1",
  gate: null,
  openArtifactId: null,
  onSelectArtifact: () => {},
  streamingArtifactId: null,
  collapsed: false,
  onToggle: () => {},
};

const art = (id: string, stage: string, title: string, at?: string) => ({
  id,
  stage,
  kind: "markdown" as const,
  title,
  content: "body",
  created_at: at,
});

function draw(props: Record<string, unknown>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      {/* @ts-expect-error — props are assembled per test */}
      <ArtifactsPanel {...base} {...props} />
    </QueryClientProvider>,
  );
}

describe("the deliverables panel", () => {
  it("labels the tab Artifacts by default, so the Copilot is untouched", () => {
    draw({ activeStage: "design", artifacts: [] });
    expect(screen.getByRole("tab", { name: /^artifacts$/i })).toBeTruthy();
  });

  it("labels the tab Deliverables when asked", () => {
    draw({ activeStage: "design", artifacts: [], tabLabel: "Deliverables" });
    expect(screen.getByRole("tab", { name: /^deliverables$/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /^artifacts$/i })).toBeNull();
  });

  it("groups agent-wise", () => {
    draw({
      activeStage: "security",
      tabLabel: "Deliverables",
      artifacts: [
        art("a", "security", "Security Report"),
        art("b", "requirements", "PRD"),
      ],
    });
    expect(screen.getByTestId("deliverable-group-security")).toBeTruthy();
    expect(screen.getByTestId("deliverable-group-requirements")).toBeTruthy();
  });

  it("orders versions newest first within an agent", () => {
    // Every version is kept, so without ordering the reader has to work out which of
    // three identically-titled reports is the current one.
    draw({
      activeStage: "security",
      tabLabel: "Deliverables",
      artifacts: [
        art("old", "security", "Security Report", "2026-09-06T10:00:00Z"),
        art("new", "security", "Security Report", "2026-09-06T14:00:00Z"),
      ],
    });
    const group = screen.getByTestId("deliverable-group-security");
    const titles = within(group).getAllByTestId("deliverable-title");
    expect(titles[0]?.getAttribute("data-id")).toBe("new");
    expect(titles[1]?.getAttribute("data-id")).toBe("old");
  });

  it("distinguishes two versions by timestamp, so the reader can tell them apart", () => {
    draw({
      activeStage: "security",
      tabLabel: "Deliverables",
      artifacts: [
        art("old", "security", "Security Report", "2026-09-06T10:00:00Z"),
        art("new", "security", "Security Report", "2026-09-06T14:00:00Z"),
      ],
    });
    const group = screen.getByTestId("deliverable-group-security");
    expect(within(group).getAllByTestId("deliverable-time")).toHaveLength(2);
  });

  it("shows no timestamp on a pointer, which has no single moment", () => {
    // The code tree is a reference to the run workspace, not a document produced at
    // a point in time. A fabricated timestamp there would read as a version.
    draw({
      activeStage: "development",
      tabLabel: "Deliverables",
      artifacts: [{
        id: "dev-code", stage: "development", kind: "code-tree" as const,
        title: "Repository code",
      }],
    });
    const group = screen.getByTestId("deliverable-group-development");
    expect(within(group).queryAllByTestId("deliverable-time")).toHaveLength(0);
  });
});
