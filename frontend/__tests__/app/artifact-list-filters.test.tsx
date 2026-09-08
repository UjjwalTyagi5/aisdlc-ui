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

/**
 * Which row is the open one.
 *
 * SELECTION LOOKED LIKE HOVER. The active row set `bg-surface-1`, which is the token the
 * hover rule sets — so the row whose story filled the detail pane was styled exactly
 * like whichever row the pointer happened to be over, and on a list of fifteen nothing
 * said which was which.
 */
describe("showing which story is open", () => {
  const selected = MIXED[1]! as { id: string };

  it("marks the open row for assistive tech", () => {
    render(
      <ArtifactList
        items={MIXED as never}
        noun="stories"
        selectedId={selected.id}
        onSelect={() => {}}
      />,
    );
    const open = screen.getAllByRole("option").find((o) => o.getAttribute("aria-selected") === "true");
    expect(open).toBeTruthy();
    expect(open?.textContent).toContain("Payments epic");
  });

  it("styles it differently from hover rather than identically", () => {
    /** THE ACTUAL BUG. `bg-surface-1` is the hover token; reusing it made the two
     *  states indistinguishable. Asserted on the class because jsdom paints nothing. */
    const { container } = render(
      <ArtifactList
        items={MIXED as never}
        noun="stories"
        selectedId={selected.id}
        onSelect={() => {}}
      />,
    );
    const buttons = Array.from(container.querySelectorAll("li button"));
    const openBtn = buttons.find((b) => b.textContent?.includes("Payments epic"));
    const otherBtn = buttons.find((b) => b.textContent?.includes("Login flow"));

    expect(openBtn?.className).toContain("bg-surface-2");
    expect(openBtn?.className).toContain("brand-bright");
    // The unselected row carries neither, so the distinction is real rather than
    // something every row happens to have.
    expect(otherBtn?.className).not.toContain("bg-surface-2");
    expect(otherBtn?.className).not.toContain("brand-bright");
  });

  it("marks nothing when no story is open", () => {
    render(<ArtifactList items={MIXED as never} noun="stories" onSelect={() => {}} />);
    const anyOpen = screen
      .getAllByRole("option")
      .some((o) => o.getAttribute("aria-selected") === "true");
    expect(anyOpen).toBe(false);
  });
});

/**
 * What the round checkboxes are for.
 *
 * They set the AGENT'S SCOPE, which is a different question from which row is open —
 * clicking a story to read it must not quietly change what the agent works on. But an
 * unlabelled circle beside a highlighted row reads as a selection radio that has stopped
 * working, which is exactly how it was reported. The only explanation was an aria-label
 * no sighted reader ever sees.
 */
describe("the scope checkboxes", () => {
  it("says what they do when none are ticked", () => {
    render(
      <ArtifactList
        items={MIXED as never}
        noun="stories"
        selectedIds={new Set()}
        onToggleSelect={() => {}}
      />,
    );
    // SINGULARISED PROPERLY. A naive `replace(/s$/, "")` on "stories" gives "storie",
    // which is what shipped for one commit and read as a typo in the product.
    expect(screen.getByText(/scope the agent to that story\./i)).toBeTruthy();
    // And says plainly that opening a story is NOT the same act.
    expect(screen.getByText(/Opening one only shows it/i)).toBeTruthy();
  });

  it("singularises a plural ending in -ies", () => {
    /** The general "strip a trailing s" rule is wrong for exactly this shape, and
     *  "stories" is the noun this component is used with most. */
    render(
      <ArtifactList
        items={[MIXED[0]] as never}
        noun="stories"
        selectedIds={new Set([(MIXED[0] as { id: string }).id])}
        onToggleSelect={() => {}}
      />,
    );
    expect(screen.getByText(/^1 story scoped to the agent\.$/i)).toBeTruthy();
  });

  it("counts them once some are ticked", () => {
    const two = new Set([(MIXED[0] as { id: string }).id, (MIXED[1] as { id: string }).id]);
    render(
      <ArtifactList
        items={MIXED as never}
        noun="stories"
        selectedIds={two}
        onToggleSelect={() => {}}
      />,
    );
    expect(screen.getByText(/2 stories scoped to the agent/i)).toBeTruthy();
  });

  it("says nothing where there are no circles", () => {
    /** Non-vacuity: the hint belongs to the checkboxes, not to every list. The Design
     *  page renders this component without `onToggleSelect`. */
    render(<ArtifactList items={MIXED as never} noun="stories" />);
    expect(screen.queryByText(/scope the agent/i)).toBeNull();
  });
});
