// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DeliveryStatusPicker } from "@/components/app/delivery-status-badge";

/**
 * The delivery-status pill on a project's Overview, reported as "not working" —
 * clicking it did nothing.
 *
 * THE POINT OF THIS SUITE is that "the button is dead" has two very different causes
 * and the fix depends on which: either the control itself never opens, or it opens
 * fine and something on the page is eating the click. Rendering it in isolation
 * separates the two, which staring at the component cannot.
 */
afterEach(cleanup);

const trigger = () => screen.getByRole("button", { name: "Change delivery status" });

describe("the delivery status picker", () => {
  it("opens its menu when clicked", async () => {
    const user = userEvent.setup();
    render(
      <DeliveryStatusPicker status="not_started" canEdit onChange={() => {}} />,
    );

    await user.click(trigger());

    expect(await screen.findByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /In progress/ })).toBeTruthy();
  });

  it("reports the status the user picked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <DeliveryStatusPicker status="not_started" canEdit onChange={onChange} />,
    );

    await user.click(trigger());
    await user.click(await screen.findByRole("menuitem", { name: /In progress/ }));

    expect(onChange).toHaveBeenCalledWith("in_progress");
  });

  it("does not re-report the status already set", async () => {
    /** Choosing the current value is a no-op, not a PATCH that changes nothing. */
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <DeliveryStatusPicker status="not_started" canEdit onChange={onChange} />,
    );

    await user.click(trigger());
    await user.click(await screen.findByRole("menuitem", { name: /Not started/ }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("renders a plain badge with no button when the viewer cannot edit", () => {
    /** A greyed-out control invites a click that will never work. */
    render(
      <DeliveryStatusPicker status="not_started" canEdit={false} onChange={() => {}} />,
    );

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/Not started/i)).toBeTruthy();
  });

  it("disables the trigger while a change is in flight", () => {
    /** Asserted on the ATTRIBUTE, not by clicking: jsdom still dispatches pointer
     *  events at a disabled button, so a click-based version of this test would
     *  report a bug that no real browser can reproduce. */
    render(
      <DeliveryStatusPicker status="not_started" canEdit busy onChange={() => {}} />,
    );

    expect((trigger() as HTMLButtonElement).disabled).toBe(true);
  });
});
