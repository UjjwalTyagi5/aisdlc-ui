/**
 * @vitest-environment jsdom
 *
 * RequirePermission guard — renders children when permission present, fallback when absent.
 *
 * Uses @testing-library/react (DOM render) because the component calls useSession()
 * (React hook). We mock @/hooks/use-session so no SessionProvider is needed.
 *
 * Session hook used: useSession() with no args → Session | null (does NOT throw).
 * The mock returns a minimal object with only the `permissions` field that
 * hasPermission reads — sufficient at runtime; TypeScript is loose on vi.mock shapes.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequirePermission } from "../restricted-access";

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ permissions: ["cost:view"] }),
}));

afterEach(cleanup);

describe("RequirePermission", () => {
  it("renders children when permission present", () => {
    render(
      <RequirePermission permission="cost:view">
        <span>secret</span>
      </RequirePermission>,
    );
    expect(screen.getByText("secret")).toBeInTheDocument();
  });
  it("renders fallback when permission absent", () => {
    render(
      <RequirePermission permission="audit:view" fallback={<span>nope</span>}>
        <span>secret</span>
      </RequirePermission>,
    );
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(screen.getByText("nope")).toBeInTheDocument();
  });
});
