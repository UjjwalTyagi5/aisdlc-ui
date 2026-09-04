// @vitest-environment jsdom
/**
 * A locked row's "Request access" button belongs beside the "No access" badge that
 * explains it, not under the agent's name.
 *
 * Below the title it made every locked row taller than the rows around it, so a rail
 * of mostly-locked agents read as a stack of forms rather than a pipeline — and the
 * button sat nowhere near the words it answers.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PhasePipeline } from "@/components/app/phase-pipeline";

afterEach(cleanup);

function renderPipeline(tileState: (p: never) => string) {
  return render(
    <PhasePipeline
      pipeline={[
        { phase: "requirements", status: "queued" },
        { phase: "design", status: "queued" },
      ] as never}
      track={"greenfield" as never}
      tileState={tileState as never}
      renderLockedAction={() => <button type="button">Request access</button>}
    />,
  );
}

describe("a locked pipeline row", () => {
  it("puts Request access in the same group as the No access badge", () => {
    renderPipeline(() => "locked");

    const button = screen.getAllByRole("button", { name: /request access/i })[0]!;
    const badge = screen.getAllByText(/no access/i)[0]!;

    // Same parent => same row-level group on the right, rather than the button
    // living in the title column and the badge in another.
    expect(button.parentElement).toBe(badge.parentElement);
  });

  it("does not nest the action inside the agent's title block", () => {
    renderPipeline(() => "locked");

    const title = screen.getAllByText("Requirements")[0]!;
    const button = screen.getAllByRole("button", { name: /request access/i })[0]!;

    // The title's column must not contain the action — that placement is what
    // stretched each locked row.
    expect(title.closest("div")?.contains(button)).toBe(false);
  });

  it("offers the action only on the rows that are locked", () => {
    // The component renders the whole track roster, not just the entries passed
    // in, so this asserts the count follows tileState rather than the pipeline.
    renderPipeline((p) => (p === "design" ? "locked" : "owner"));

    expect(screen.getAllByRole("button", { name: /request access/i })).toHaveLength(1);
    expect(screen.getAllByText(/no access/i)).toHaveLength(1);
  });
});
