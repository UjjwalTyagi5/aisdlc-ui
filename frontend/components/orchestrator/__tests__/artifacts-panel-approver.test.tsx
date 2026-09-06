// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";

/**
 * The panel has NO approver or gate concept at all.
 *
 * This file used to guard a two-sided rule. A Phase 1 fix had hidden the "Who
 * approves" section whenever `gate` was null — correct for the Orchestrator, which has
 * no gates, and wrong for the Copilot, whose `gate` was ALSO null for most of a run,
 * so the section silently vanished from a live page. The fix made visibility the
 * caller's decision (`showApprover`) rather than an inference from `gate`.
 *
 * Phase 5 deleted the Copilot, so the only caller that ever passed `showApprover`
 * is gone. The rule survives in a stronger form: the section cannot render because it
 * does not exist, and the props that drove it do not exist either. A guarantee held by
 * structure beats one held by a default.
 *
 * Asserted on the rendered panel AND on its prop surface, because "we removed the
 * feature" is only true while nobody adds the prop back.
 */

vi.mock("@/lib/api/runs", () => ({ getRun: async () => ({ id: "run-1" }) }));
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
  fireEvent.click(screen.getByRole("tab", { name: "Context" }));
}

describe("the Context tab has no approver section", () => {
  it("renders no approver copy", () => {
    renderPanel();
    expect(screen.queryByText(/who approves/i)).toBeNull();
    expect(screen.queryByText(/approval-required/i)).toBeNull();
    expect(screen.queryByText(/product manager/i)).toBeNull();
  });

  it("renders none even if a caller passes the retired props", () => {
    // They are no longer declared, so this is what a stale call site would do. It must
    // be inert rather than quietly reviving a surface the Orchestrator must not have.
    renderPanel({ showApprover: true, gate: { owner_role: "product_manager" } });
    expect(screen.queryByText(/who approves/i)).toBeNull();
  });

  it("does not declare gate or showApprover props any more", () => {
    // Structural, not behavioural: the section is gone because the concept is gone.
    // Without this, someone re-adding the prop would find both tests above still green
    // until they also re-added the section.
    const src = readFileSync("components/orchestrator/artifacts-panel.tsx", "utf8");
    expect(src).not.toContain("showApprover");
    expect(src).not.toContain("GateState");
  });
});
