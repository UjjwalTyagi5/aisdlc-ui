// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";

/**
 * The panel has NO approver, NO gate and NO Context tab.
 *
 * This file has narrowed twice, each time because the thing it guarded stopped
 * existing rather than stopped misbehaving.
 *
 * 1. Phase 1 hid the "Who approves" section whenever `gate` was null — correct for the
 *    Orchestrator, which has no gates, and wrong for the Copilot, whose `gate` was also
 *    null for most of a run, so the section silently vanished from a live page. The fix
 *    made visibility the caller's decision (`showApprover`).
 * 2. Phase 5 deleted the Copilot, so the only caller that ever passed `showApprover`
 *    went with it, and the props were removed.
 * 3. 2026-09-07: the Context tab itself was removed at the user's request — "it is not
 *    needed, it just makes things confusing". It restated the run's stage and gate
 *    position, which is the linear-pipeline framing this engine exists to remove.
 *
 * The two tests that used to render the tab and check the copy was absent are gone
 * with it: a tab that cannot be opened is a stronger guarantee than one that opens and
 * shows nothing. What remains is structural, because "we removed the feature" is only
 * true while nobody adds it back.
 */

vi.mock("@/components/orchestrator/artifact-viewer", () => ({
  ArtifactViewer: () => <div data-testid="viewer" />,
}));

class _ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", _ResizeObserver);

afterEach(cleanup);

function renderPanel(overrides: Record<string, unknown> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={client}>
      <ArtifactsPanel
        runId=""
        activeStage="requirements"
        artifacts={[]}
        openArtifactId={null}
        onSelectArtifact={() => {}}
        streamingArtifactId={null}
        collapsed={false}
        onToggle={() => {}}
        {...overrides}
      />
    </QueryClientProvider>,
  );
}

const SOURCE = readFileSync("components/orchestrator/artifacts-panel.tsx", "utf8");

describe("the panel's retired surfaces", () => {
  it("offers no Context tab", () => {
    renderPanel();
    expect(screen.queryByRole("tab", { name: /context/i })).toBeNull();
  });

  it("still offers the two tabs that carry this run's own output", () => {
    // The removal must not take the panel with it. Deliverables is what the run
    // produced; Activity is what it is doing. `tabLabel` is passed because the cockpit
    // passes it — the component's own default is still the older "Artifacts".
    renderPanel({ tabLabel: "Deliverables" });
    expect(screen.getByRole("tab", { name: /deliverables/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /activity/i })).toBeInTheDocument();
  });

  it("renders no approver copy anywhere", () => {
    renderPanel({ showApprover: true, gate: { owner_role: "product_manager" } });
    // Retired props on a stale call site must be inert rather than quietly reviving a
    // surface the Orchestrator must not have.
    expect(screen.queryByText(/who approves/i)).toBeNull();
    expect(screen.queryByText(/approval-required/i)).toBeNull();
  });

  it("declares no gate, approver or Context machinery", () => {
    // Structural, not behavioural: these are gone because the concepts are gone.
    // Without this, someone re-adding a prop would find the tests above still green
    // until they also re-added the surface that reads it.
    expect(SOURCE).not.toContain("showApprover");
    expect(SOURCE).not.toContain("GateState");
    expect(SOURCE).not.toContain("ContextTab");
  });

  it("no longer polls the run detail endpoint", () => {
    // The 8-second `getRun` poll existed only to feed the Context tab. Leaving it
    // would keep a request nothing renders — invisible, and paid for on every open
    // panel.
    expect(SOURCE).not.toContain("getRun");
  });
});
