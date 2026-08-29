// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

// The page reads the viewer's platform role via useRawSession +
// effectivePlatformRole (page-level connector:view gate and the
// granted/org-admin branching), separately from useAccessScope (which backs
// useScopedBusinessUnits and RequestAccessButton's canRaiseType check below).
// All three are stubbed to a bu_admin so the page renders its real ungranted
// content instead of RestrictedAccess.
vi.mock("@/components/auth/session-provider", () => ({
  useRawSession: () => ({ permissions: ["connector:view"] }),
}));
vi.mock("@/lib/auth/permissions", () => ({
  hasPermission: () => true,
}));
vi.mock("@/lib/auth/effective-role", () => ({
  effectivePlatformRole: () => "bu_admin",
}));
vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: () => ({
    role: "bu_admin",
    scope: {},
    bindings: [],
    businessUnitIds: ["bu-1"],
    managedBusinessUnitIds: ["bu-1"],
    isOrgWide: false,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("@/lib/api/connectors", () => ({
  // Empty catalogue + empty grants -> every catalogue tile (Jira included)
  // renders as ungranted, with its own Request access button.
  listConnectors: vi.fn().mockResolvedValue([]),
  listConnectorGrants: vi.fn().mockResolvedValue([]),
  disconnectConnector: vi.fn(),
  setConnectorCredentials: vi.fn(),
}));

vi.mock("@/lib/api/integration-access", () => ({
  listIntegrationAccess: vi.fn().mockResolvedValue([
    {
      kind: "mcp",
      id: "mcp-postgres",
      name: "Postgres MCP",
      description: null,
      origin: "organization",
      onboarded: true,
      units: [],
      // 0 -> not granted to this bu_admin's unit, so the row renders ungranted
      // with its own Request access button too.
      grantedUnitCount: 0,
      projectCount: 0,
      tools: [],
    },
  ]),
}));

// A plain stub, not a rendered dialog — RequestAccessButton always mounts
// RaiseRequestDialog (open={false} until clicked), so every tile's prefill is
// observable on render without needing to open anything. Clicking a button
// still flips that instance's `open` to true, which shows up as a second
// call with the same prefill and open: true, so filtering on `open === true`
// isolates the one the test actually clicked. Same technique as
// model-availability-card.test.tsx.
const raiseRequestDialogSpy = vi.fn();
vi.mock("@/components/requests/raise-request-dialog", () => ({
  RaiseRequestDialog: (props: unknown) => {
    raiseRequestDialogSpy(props);
    return null;
  },
}));

import IntegrationsPage from "@/app/(app)/integrations/page";

afterEach(cleanup);

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <IntegrationsPage />
    </QueryClientProvider>,
  );
}

function lastOpenedPrefill() {
  const opened = raiseRequestDialogSpy.mock.calls
    .map((call) => call[0] as { open: boolean; prefill?: unknown })
    .filter((props) => props.open);
  return opened.at(-1)?.prefill as
    | { type?: string; targetId?: string; accessLevel?: string }
    | undefined;
}

describe("integrations page — request prefill", () => {
  it("connector tile's Request access button carries targetId=kind and a read accessLevel", async () => {
    renderPage();

    const jiraHeading = await screen.findByText("Jira");
    const card = jiraHeading.closest("li");
    expect(card).not.toBeNull();

    const button = within(card as HTMLElement).getByRole("button", { name: /request access/i });
    await userEvent.click(button);

    const prefill = lastOpenedPrefill();
    expect(prefill).toMatchObject({
      type: "connector_access",
      targetId: "jira",
      accessLevel: "read",
    });
  });

  it("MCP server row's Request access button carries targetId=the row's id", async () => {
    renderPage();

    const rowHeading = await screen.findByText("Postgres MCP");
    const card = rowHeading.closest("li");
    expect(card).not.toBeNull();

    const button = within(card as HTMLElement).getByRole("button", { name: /request access/i });
    await userEvent.click(button);

    const prefill = lastOpenedPrefill();
    expect(prefill).toMatchObject({
      type: "mcp_server",
      targetId: "mcp-postgres",
    });
  });

  it("section-level 'Request an MCP server' button carries NO targetId or accessLevel — there is no server in view yet", async () => {
    renderPage();

    // Distinct label from the per-row tiles' default "Request access", so this
    // targets the section-level button specifically, not a row's own tile.
    const button = await screen.findByRole("button", { name: /request an mcp server/i });
    await userEvent.click(button);

    const prefill = lastOpenedPrefill();
    expect(prefill).toMatchObject({ type: "mcp_server" });
    // Task 7's effect must handle this no-target case gracefully — a future
    // "fix" that seeds a targetId here would silently break that assumption.
    expect(prefill).not.toHaveProperty("targetId");
    expect(prefill).not.toHaveProperty("accessLevel");
  });
});
