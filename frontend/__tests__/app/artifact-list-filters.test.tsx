// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ArtifactList } from "@/components/app/artifact-list";

/**
 * The filters on a pulled-stories list.
 *
 * THE OLD TYPE FILTER READ THE WRONG FIELD. It filtered `artifact.type`, which
 * `story_artifacts_from_run` sets to "story" for every row a board pull produces — so
 * on Requirements it could never separate anything, while offering ten options of which
 * nine could only empty the screen. What actually varies is the BOARD's type, carried on
 * the body as `workItemType`: `ingest_board` pulls every work item, so an Epic and three
 * Tasks about configuring the board itself arrive alongside real stories.
 */
afterEach(cleanup);

// Radix Select drives itself with Pointer Events APIs jsdom does not implement, so the
// menu never opens and the test looks like a filtering bug. Stubbed rather than dropped:
// "does choosing Epic actually narrow the list" is the assertion worth having.
beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
});

type Row = Record<string, unknown>;

const story = (title: string, workItemType?: string): Row => ({
  id: `run1:story:${title}`,
  projectId: "p1",
  runId: "run1",
  type: "story",
  phase: "requirements",
  title,
  version: 1,
  contentHash: "h",
  status: "draft",
  body: { kind: "story", title, description: "", acceptanceCriteria: [], workItemType },
  createdBy: "agent",
  createdAt: "2026-09-07T00:00:00Z",
  updatedAt: "2026-09-07T00:00:00Z",
});

const MIXED = [
  story("Login flow", "User Story"),
  story("Payments epic", "Epic"),
  story("Configure the board", "Task"),
  story("Crash on submit", "Bug"),
];

describe("filtering a story list", () => {
  it("calls them stories rather than artifacts", () => {
    /** The box read "Filter artifacts…" under a heading saying "Stories (15)", leaving
     *  the reader to work out the two were the same list. */
    render(<ArtifactList items={MIXED as never} noun="stories" />);
    expect(screen.getByLabelText("Filter stories")).toBeTruthy();
  });

  it("still says artifacts where that is what they are", () => {
    /** Non-vacuity for the above: the noun is a prop, not a rename. */
    render(<ArtifactList items={MIXED as never} />);
    expect(screen.getByLabelText("Filter artifacts")).toBeTruthy();
  });

  it("offers the board's work-item types, which are what differ", () => {
    render(<ArtifactList items={MIXED as never} noun="stories" />);
    expect(screen.getByLabelText("Filter by work item type")).toBeTruthy();
  });

  it("hides the artifact-type filter when every row is the same type", () => {
    /** All four are `type: "story"`. A dropdown that can only empty the screen teaches
     *  people that filters do nothing here. */
    render(<ArtifactList items={MIXED as never} noun="stories" />);
    expect(screen.queryByLabelText("Filter by type")).toBeNull();
  });

  it("narrows to one work-item type", async () => {
    const user = userEvent.setup();
    render(<ArtifactList items={MIXED as never} noun="stories" />);

    await user.click(screen.getByLabelText("Filter by work item type"));
    await user.click(await screen.findByRole("option", { name: "Epic" }));

    expect(screen.getByText("Payments epic")).toBeTruthy();
    expect(screen.queryByText("Login flow")).toBeNull();
    expect(screen.queryByText("Crash on submit")).toBeNull();
  });

  it("hides the work-item filter when the board returned only one kind", () => {
    render(
      <ArtifactList
        items={[story("a", "User Story"), story("b", "User Story")] as never}
        noun="stories"
      />,
    );
    expect(screen.queryByLabelText("Filter by work item type")).toBeNull();
  });

  it("keeps the list a fixed height however many stories arrive", () => {
    /** Fifteen stories ran past the fold and took the rest of the column with them. */
    const { container } = render(<ArtifactList items={MIXED as never} noun="stories" />);
    const list = container.querySelector("ul");
    expect(list?.className).toContain("overflow-y-auto");
  });
});
