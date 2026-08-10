// @vitest-environment jsdom
/**
 * How the reach control behaves as the organisation grows.
 *
 * The control has two renderings of the same list on purpose, and the seed
 * fixtures only ever exercise one of them: with three Business Units you always
 * get chips, so the branch written for twenty ships unexercised and unseen.
 * These render it directly, which is the only place the large-tenant layout is
 * checked at all.
 */
// Explicit: vitest has no react plugin configured, so JSX compiles to the
// classic React.createElement rather than the automatic runtime.
import * as React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import { GrantVisibilityControl } from "@/components/app/grant-visibility-control";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

afterEach(cleanup);

const units = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ id: `ws_${i}`, displayName: `Unit ${i}` }));

function renderControl(count: number, extra?: { selected?: string[]; optional?: boolean }) {
  const onChange = vi.fn();
  render(
    <GrantVisibilityControl
      idPrefix="t"
      value={{ visibility: "specific", businessUnitIds: extra?.selected ?? [] }}
      workspaces={units(count)}
      optional={extra?.optional}
      onChange={onChange}
    />,
  );
  return { onChange };
}

const searchBox = () =>
  screen.queryByLabelText(`Search ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`);

describe("naming which units a grant reaches", () => {
  it("shows a handful of units whole, with no search box in the way", () => {
    renderControl(3);
    expect(searchBox()).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Unit 2" })).toBeTruthy();
  });

  it("switches to a searchable list once the list stops being scannable", () => {
    renderControl(20);
    expect(searchBox()).not.toBeNull();
  });

  it("filters to matches as you type, so cost of use is flat in tenant size", () => {
    renderControl(20);
    fireEvent.change(searchBox()!, { target: { value: "Unit 17" } });
    expect(screen.getByRole("checkbox", { name: "Unit 17" })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: "Unit 3" })).toBeNull();
  });

  it("keeps a filtered-away selection, rather than silently dropping it", () => {
    // The count is the guard: search narrows what you SEE, and a control that
    // let the view decide what is selected would revoke units off-screen.
    renderControl(20, { selected: ["ws_3", "ws_4"] });
    fireEvent.change(searchBox()!, { target: { value: "Unit 17" } });
    expect(screen.getByText(`2 of 20 named`)).toBeTruthy();
  });

  it("still reports a selection made through the filter", () => {
    const { onChange } = renderControl(20);
    fireEvent.change(searchBox()!, { target: { value: "Unit 12" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Unit 12" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ businessUnitIds: ["ws_12"] }),
    );
  });
});

describe("naming nobody", () => {
  it("warns when a grant is being edited — an empty list revokes it everywhere", () => {
    renderControl(3);
    expect(screen.getByText(/Nobody can use this until you name/)).toBeTruthy();
  });

  it("reads as a deferred decision when onboarding, where it is a real choice", () => {
    renderControl(3, { optional: true });
    expect(screen.queryByText(/Nobody can use this until you name/)).toBeNull();
    expect(screen.getByText(/registered but reaches no/)).toBeTruthy();
  });
});
