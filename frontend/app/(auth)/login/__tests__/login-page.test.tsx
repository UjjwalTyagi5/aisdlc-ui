// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type * as EmailPasswordFormModuleNS from "@/app/(auth)/login/email-password-form";

type EmailPasswordFormModule = typeof EmailPasswordFormModuleNS;

/**
 * `/login` is the same sign-in as the landing popup, and must look like it.
 *
 * The two had drifted once already — `sign-in-brand-panel.tsx` was written to stop it,
 * by sharing the marketing half. The half that actually matters kept drifting anyway:
 * the popup became a single centered card while the route stayed a two-column layout
 * with a marketing panel beside it. Reported after a password reset, which is exactly
 * where it hurts — an expired session and every emailed link land on this route, so the
 * drifted one is the first thing a returning user sees.
 *
 * The fix is not "make the classes match". It is one module owning the heading, the
 * blurb, the auth-mode branch and the terms, with each host supplying only its own
 * frame. These tests pin the shared content and the things the page must keep that the
 * popup never had — the error banner and the `from` redirect.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/login",
}));

/**
 * `redirectTo` leaves no trace in the DOM — the form keeps it in a closure and hands it
 * to `router.push` after a successful sign-in. Asserted on a hidden input first, which
 * this form does not have: a fixture invented to match the assertion. The real form
 * still renders; only the prop is observed on the way through.
 */
let lastRedirectTo: string | undefined;
vi.mock("@/app/(auth)/login/email-password-form", async (importOriginal) => {
  const mod = await importOriginal<EmailPasswordFormModule>();
  return {
    EmailPasswordForm: (props: { redirectTo: string }) => {
      lastRedirectTo = props.redirectTo;
      return <mod.EmailPasswordForm {...props} />;
    },
  };
});

vi.mock("@/lib/auth/mode", () => ({
  AUTH_MODE: "local" as const,
  isLocalAuth: true,
  isMockAuth: false,
  isAuth0: false,
  isOidcEnabled: false,
}));

import LoginPage from "@/app/(auth)/login/page";
import { LoginDialog } from "@/components/landing/login-dialog";

afterEach(cleanup);

async function renderPage(params: { from?: string; error?: string } = {}) {
  return render(await LoginPage({ searchParams: Promise.resolve(params) }));
}

// ── the popup's shape ─────────────────────────────────────────────────────────
describe("the sign-in route matches the landing popup", () => {
  it("shows the heading, the blurb and the credential fields", async () => {
    await renderPage();

    expect(screen.getByText(/sign in to your business unit/i)).toBeInTheDocument();
    expect(
      screen.getByText(/use the email and password set up by your administrator/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/work email/i)).toBeInTheDocument();
  });

  it("drops the marketing column", async () => {
    // THE REPORTED DIFFERENCE. The popup is the form and nothing else; this route
    // carried three selling points beside it in a second column.
    await renderPage();

    expect(screen.queryByText(/thirteen agents, one control plane/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/plugs into your existing tools/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/enterprise-ready on day one/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/soc 2 type i/i)).not.toBeInTheDocument();
  });

  it("carries the brand lockup once, the way the popup does", async () => {
    // Twice was the old layout: the marketing column had one and the sign-in column
    // had a second for narrow screens.
    await renderPage();

    expect(screen.getAllByText("SDLC Platform")).toHaveLength(1);
  });

  it("contains its own glow, rather than letting it cover the page", async () => {
    // The card's mesh layer is `absolute inset-0`, so the card must be the positioned
    // ancestor. Without `relative` the glow resolves against `main` and washes the
    // whole viewport. It cannot live in the shared surface — see the dialog, which
    // `relative` un-centers.
    await renderPage();

    const card = screen.getByTestId("sign-in-card");
    expect(card.className.split(/\s+/)).toContain("relative");
    expect(card.querySelector(".bg-mesh")).toBeInTheDocument();
  });

  it("keeps the terms line", async () => {
    await renderPage();

    expect(screen.getByRole("link", { name: /terms of service/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /privacy policy/i })).toBeInTheDocument();
  });

  it("says the same words as the popup", async () => {
    // One module owns these. Asserted across both hosts, because matching them by
    // hand is what drifted the first time.
    const page = await renderPage();
    const pageHeading = screen.getByText(/sign in to your business unit/i).textContent;
    const pageBlurb = screen.getByText(/use the email and password/i).textContent;
    page.unmount();

    render(<LoginDialog open onOpenChange={() => {}} />);

    expect(screen.getByText(/sign in to your business unit/i).textContent).toBe(pageHeading);
    expect(screen.getByText(/use the email and password/i).textContent).toBe(pageBlurb);
  });
});

// ── what the page has that the popup does not ────────────────────────────────
describe("what only the route needs", () => {
  it("explains an expired session", async () => {
    // This route is where `?error=invalid_session` lands. Losing the banner while
    // restyling would drop a returning user onto a bare form with no reason given.
    await renderPage({ error: "invalid_session" });

    expect(screen.getByText(/your session expired/i)).toBeInTheDocument();
    expect(screen.getByText(/for security, we signed you out/i)).toBeInTheDocument();
  });

  it("shows no banner when nothing went wrong", async () => {
    await renderPage();

    expect(screen.queryByText(/your session expired/i)).not.toBeInTheDocument();
  });

  it("ignores an error code it does not recognise", async () => {
    await renderPage({ error: "../../etc/passwd" });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the user back where they were headed", async () => {
    // `?from=` is why this route exists rather than a link to the landing popup.
    lastRedirectTo = undefined;
    await renderPage({ from: "/projects/42/orchestrator" });

    expect(lastRedirectTo).toBe("/projects/42/orchestrator");
  });

  it("defaults to the dashboard when it was reached directly", async () => {
    lastRedirectTo = undefined;
    await renderPage();

    expect(lastRedirectTo).toBe("/dashboard");
  });
});

/**
 * The one failure jsdom cannot reach.
 *
 * `/login` is a server component and CALLS `signInTitle()` / `signInDescription()`.
 * Across a client boundary those are not functions but serialized references, and Next
 * throws "Attempted to call signInTitle() from the server". Every test above passed
 * with the route returning a 500, because jsdom renders both sides in one process and
 * enforces no boundary. Only serving the page found it.
 */
describe("the server/client boundary", () => {
  it("keeps the shared sign-in content importable from a server component", async () => {
    const fs = await import("node:fs/promises");
    const source = await fs.readFile("components/landing/sign-in-content.tsx", "utf-8");

    expect(source).not.toMatch(/^\s*["']use client["']/m);
  });

  it("keeps the route a server component, so `from` is read on the server", async () => {
    // A "use client" here would also make `searchParams` a promise the page cannot
    // await, and push the redirect decision into the browser.
    const fs = await import("node:fs/promises");
    const source = await fs.readFile("app/(auth)/login/page.tsx", "utf-8");

    expect(source).not.toMatch(/^\s*["']use client["']/m);
  });
});
