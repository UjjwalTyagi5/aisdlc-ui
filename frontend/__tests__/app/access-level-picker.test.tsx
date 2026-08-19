// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AccessLevelPicker } from "@/components/app/access-level-picker";

/**
 * The control that has to keep read and write INCOMPARABLE on screen.
 *
 * A slider or a stepper would state that write is more than read, which is exactly
 * the escalation the model exists to prevent — so the interesting assertions here are
 * about what the ceiling does, not about which button is highlighted.
 */
// This suite renders the same control repeatedly; without cleanup every query
// matches the previous render too and fails with "found multiple elements".
afterEach(cleanup);

const radio = (name: string) =>
  screen.getByRole("radio", { name }) as HTMLButtonElement;

describe("choosing an access level", () => {
  it("offers the three levels as peers", () => {
    render(<AccessLevelPicker value="read" onChange={() => {}} />);
    for (const label of ["Read only", "Write only", "Read and write"]) {
      expect(radio(label)).toBeTruthy();
    }
  });

  it("marks only the current level as chosen", () => {
    render(<AccessLevelPicker value="write" onChange={() => {}} />);
    expect(radio("Write only").getAttribute("aria-checked")).toBe("true");
    expect(radio("Read only").getAttribute("aria-checked")).toBe("false");
  });

  it("reports the level the user picked", async () => {
    const onChange = vi.fn();
    render(<AccessLevelPicker value="read" onChange={onChange} />);
    await userEvent.click(radio("Read and write"));
    expect(onChange).toHaveBeenCalledWith("read_write");
  });

  it("disables what the ceiling forbids rather than hiding it", async () => {
    // A unit holding read-only: a project under it cannot be given write.
    const onChange = vi.fn();
    render(<AccessLevelPicker value="read" ceiling="read" onChange={onChange} />);

    // Still VISIBLE — an admin who cannot find the option learns nothing; one who
    // sees it greyed out learns the limit is real.
    const write = radio("Write only");
    expect(write).toBeTruthy();
    expect(write.disabled).toBe(true);

    await userEvent.click(write);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("treats read and write as incomparable under a ceiling", () => {
    // Under a WRITE-only ceiling, read is forbidden — the mirror case, and the one
    // an ordered control would get wrong by letting read through as "less".
    render(<AccessLevelPicker value="write" ceiling="write" onChange={() => {}} />);
    expect(radio("Read only").disabled).toBe(true);
    expect(radio("Read and write").disabled).toBe(true);
    expect(radio("Write only").disabled).toBe(false);
  });

  it("allows everything under a read+write ceiling", () => {
    render(<AccessLevelPicker value="read" ceiling="read_write" onChange={() => {}} />);
    for (const label of ["Read only", "Write only", "Read and write"]) {
      expect(radio(label).disabled).toBe(false);
    }
  });

  it("says why an option is blocked", () => {
    render(<AccessLevelPicker value="read" ceiling="read" onChange={() => {}} />);
    expect(radio("Read and write").getAttribute("title")).toContain(
      "cannot be given more",
    );
  });

  it("is inert when disabled", async () => {
    const onChange = vi.fn();
    render(<AccessLevelPicker value="read" disabled onChange={onChange} />);
    await userEvent.click(radio("Write only"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
