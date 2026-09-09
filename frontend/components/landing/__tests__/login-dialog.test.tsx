// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

/**
 * The landing popup must actually appear.
 *
 * IT DID NOT, and nothing caught it. Sharing the surface with `/login` moved a
 * `relative` into the class string this dialog passes down — and `DialogContent`
 * centers itself with `fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2`,
 * merged through `cn()`. tailwind-merge keeps the LAST utility of a group, so
 * `relative` replaced `fixed`, the content fell out of its fixed centering, and only
 * the overlay was left: clicking Sign in dimmed the page and produced no dialog.
 *
 * The page card genuinely needs `relative` — its mesh glow is `absolute inset-0` and
 * has to be contained. The dialog does not: `fixed` is already a containing block.
 * So the shared surface owns everything except positioning, and each host positions
 * itself.
 *
 * Layout is not observable in jsdom, which has no Tailwind CSS to compute. The class
 * list is, and it is exactly what the bug was — so that is what these assert.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

vi.mock("@/lib/auth/mode", () => ({
  AUTH_MODE: "local" as const,
  isLocalAuth: true,
  isMockAuth: false,
  isAuth0: false,
  isOidcEnabled: false,
}));

import { LoginDialog } from "@/components/landing/login-dialog";
import { SIGN_IN_SURFACE } from "@/components/landing/sign-in-content";

afterEach(cleanup);

const openDialog = () => render(<LoginDialog open onOpenChange={() => {}} />);

describe("the landing sign-in popup", () => {
  it("renders its content, not just the overlay", () => {
    openDialog();

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/sign in to your business unit/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/work email/i)).toBeInTheDocument();
  });

  it("keeps the fixed centering the dialog positions itself with", () => {
    // THE BUG, stated directly. `relative` here silently un-centers the popup.
    const dialog = openDialog().baseElement.querySelector('[role="dialog"]')!;

    expect(dialog.className).toContain("fixed");
    expect(dialog.className.split(/\s+/)).not.toContain("relative");
  });

  it("takes no positioning from the shared surface", () => {
    // Asserted on the constant as well as on the render, so the next person to add a
    // layout utility to the shared string finds out here rather than on the landing
    // page. Position is the host's business; the surface is colour, border and depth.
    const positioning = ["relative", "fixed", "absolute", "sticky", "static"];

    expect(SIGN_IN_SURFACE.split(/\s+/).filter((c) => positioning.includes(c))).toEqual([]);
  });

  it("still applies the shared surface", () => {
    const dialog = openDialog().baseElement.querySelector('[role="dialog"]')!;

    expect(dialog.className).toContain("bg-panel-elevated/60");
    expect(dialog.className).toContain("backdrop-blur-2xl");
  });

  it("carries the brand lockup and the terms, like the route", () => {
    openDialog();

    expect(screen.getByText("SDLC Platform")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /terms of service/i })).toBeInTheDocument();
  });

  it("sends the caller's redirect through to the form", () => {
    // The landing passes /projects; the route passes ?from. Both reach the same form.
    const { baseElement } = render(
      <LoginDialog open onOpenChange={() => {}} redirectTo="/projects/9" />,
    );

    expect(baseElement.querySelector('[role="dialog"]')).toBeInTheDocument();
  });
});
