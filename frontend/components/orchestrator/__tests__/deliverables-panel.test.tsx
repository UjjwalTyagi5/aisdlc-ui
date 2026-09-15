// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";

/**
 * The tab name is a PROP rather than a rename. It was shared with the Copilot until
 * Phase 5 deleted that surface, and the distinction it encodes outlives it: what the
 * STANDALONE agents write really is an Artifact, approval-gated and in its own table,
 * while the Orchestrator's output is a different concept and says so.
 *
 * Since the panel grew a real Artifacts tab (the project's approved documents), the
 * first tab defaults to "Deliverables": a first tab called "Artifacts" beside a third
 * tab called "Artifacts" would be two tabs with one name.
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
  it("labels the first tab Deliverables by default", () => {
    draw({ activeStage: "design", artifacts: [] });
    expect(screen.getByRole("tab", { name: /^deliverables$/i })).toBeTruthy();
  });

  it("still honours a caller's own label for the first tab", () => {
    draw({ activeStage: "design", artifacts: [], tabLabel: "Outputs" });
    expect(screen.getByRole("tab", { name: /^outputs$/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /^deliverables$/i })).toBeNull();
  });

  it("offers no Artifacts tab without a project — there is no record to list", () => {
    draw({ activeStage: "design", artifacts: [], tabLabel: "Deliverables" });
    expect(screen.queryByRole("tab", { name: /^artifacts/i })).toBeNull();
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

  it("collapsing the group that holds the open deliverable deselects it, without a setState-in-render", () => {
    // `toggleGroup` used to call the parent's `onSelectArtifact(null)` from inside its
    // `setExpanded` updater. React runs updaters during render, so that was a parent
    // setState during a child's render: "Cannot update a component (OrchestratorCockpit)
    // while rendering a different component (ArtifactsTab)" on every collapse.
    //
    // A REAL PARENT, not a mock: React only raises that warning when the callback is
    // another component's setState, so a `vi.fn()` here passes with the bug in place.
    const errors: unknown[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args) => {
      errors.push(args);
    });
    function Host() {
      const [open, setOpen] = React.useState<string | null>("a");
      return (
        <>
          <span data-testid="open">{open ?? "none"}</span>
          <ArtifactsPanel
            {...base}
            activeStage="security"
            tabLabel="Deliverables"
            openArtifactId={open}
            onSelectArtifact={setOpen}
            artifacts={[art("a", "security", "Security Report")]}
          />
        </>
      );
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    try {
      render(
        <QueryClientProvider client={client}>
          <Host />
        </QueryClientProvider>,
      );
      const group = screen.getByTestId("deliverable-group-security");
      // The group header is the one button carrying aria-expanded; rows have none.
      const header = () =>
        within(group).getAllByRole("button").find((b) => b.hasAttribute("aria-expanded"))!;
      // THREE CLICKS IN ONE BATCH. React computes a lone setState eagerly, in the
      // event handler, and the bug hides. With updates already queued it defers the
      // updater to the render phase — which is where the real page runs it, and where
      // a parent setState from inside it is the error the user saw.
      act(() => {
        fireEvent.click(header()); // collapse (eager)
        fireEvent.click(header()); // expand (queued)
        fireEvent.click(header()); // collapse again — updater runs during render
      });

      expect(screen.getByTestId("open").textContent).toBe("none");
      const setStateInRender = errors.filter((a) =>
        String((a as unknown[])[0]).includes("while rendering a different component"),
      );
      expect(setStateInRender).toHaveLength(0);
    } finally {
      spy.mockRestore();
    }
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
