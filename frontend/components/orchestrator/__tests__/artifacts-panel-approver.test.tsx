// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";
import type { GateState } from "@/lib/orchestrator/chat-types";

/**
 * Regression coverage for the Context tab's "Who approves" section.
 *
 * A prior fix correctly stripped invented gate language from the Orchestrator
 * (which has no gates at all) by hiding the section whenever `gate` was null.
 * But the Copilot's `gate` is ALSO null for most of a run — it starts null and
 * is reset on every stage change and after every gate decision (see
 * lib/copilot/use-copilot.ts) — so that fix silently deleted the section from
 * the Copilot too, for every state except the brief window a gate is pending.
 *
 * Visibility must be the CALLER's decision (`showApprover`), not an inference
 * from `gate`: the Orchestrator passes `showApprover={false}` (or omits it)
 * and must never render this section in any state; the Copilot passes
 * `showApprover` and falls back to the stage's real owner role when `gate` is
 * null, never to an invented name like "Product Manager".
 */

afterEach(cleanup);

type PanelProps = React.ComponentProps<typeof ArtifactsPanel>;

function renderPanel(overrides: Partial<PanelProps>) {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <ArtifactsPanel
        runId=""
        activeStage="requirements"
        gate={null}
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

describe("ArtifactsPanel Context tab — Who approves", () => {
  it("never renders for the Orchestrator when showApprover is omitted", () => {
    // No showApprover passed at all — the component's own default.
    renderPanel({ gate: null, activeStage: "requirements" });
    expect(screen.queryByText("Who approves")).not.toBeInTheDocument();
  });

  it("never renders for the Orchestrator when showApprover is explicitly false", () => {
    // What cockpit.tsx actually passes.
    renderPanel({ showApprover: false, gate: null, activeStage: "requirements" });
    expect(screen.queryByText("Who approves")).not.toBeInTheDocument();
  });

  it("renders for the Copilot even when gate is null, falling back to the stage owner", () => {
    // This is the Copilot's steady state between gates — `useCopilot` keeps
    // `gate` at null except for the brief awaiting-approval window.
    renderPanel({ showApprover: true, gate: null, activeStage: "requirements" });

    expect(screen.getByText("Who approves")).toBeInTheDocument();
    // The Requirements stage's real PRD owner (BA) — never the invented
    // "Product Manager" fallback the earlier regression would have shown.
    expect(screen.getByText("BA (Business Analyst)")).toBeInTheDocument();
    expect(screen.queryByText("Product Manager")).not.toBeInTheDocument();
  });

  it("renders for the Copilot with a real gate, naming the gate's own owner", () => {
    const gate: GateState = {
      stage: "requirements",
      status: "awaiting_gate",
      owner_role: "ba",
      can_approve: true,
    };
    renderPanel({ showApprover: true, gate, activeStage: "requirements" });

    expect(screen.getByText("Who approves")).toBeInTheDocument();
    expect(screen.getByText("BA (Business Analyst)")).toBeInTheDocument();
  });
});
