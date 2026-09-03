// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DeploymentRequest } from "@/lib/schemas/deployment";
import live from "../fixtures/live-deployments.json";

/**
 * The contract between what the backend really sends and what this UI accepts.
 *
 * WHY A CAPTURED PAYLOAD AND NOT ANOTHER FIXTURE. Every other test in this suite uses
 * objects I wrote, which proves the component agrees with me — not that it agrees with
 * the server. A field the backend renames, a null it starts sending, or an enum value
 * the zod schema does not list all fail the same way: the query throws on parse and the
 * Deployments tab shows an error instead of the queue, with nothing in the component
 * tests going red.
 *
 * live-deployments.json was captured from a running backend
 * (GET /deployment/{project}/deployments) against a real project, and holds the three
 * states that flow produced: pending, approved, and an error whose outcome carries the
 * honest "nothing was sent" reporting.
 *
 * If this file fails after a backend change, the UI is broken — not the test.
 */

const listDeployments = vi.fn();
vi.mock("@/lib/api/deployment", () => ({
  listDeployments: (...a: unknown[]) => listDeployments(...a),
  approveDeployment: vi.fn(),
  rejectDeployment: vi.fn(),
  executeDeployment: vi.fn(),
  refreshDeployment: vi.fn(),
}));
vi.mock("@/components/auth/require-role", () => ({
  RequireRole: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { DeploymentApprovals } from "@/components/app/deployment-approvals";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const rows = live as unknown[];

describe("the payload the backend actually sends", () => {
  it("has something to check", () => {
    expect(rows.length).toBeGreaterThan(0);
  });

  it("satisfies the schema the UI parses it with, row for row", () => {
    for (const row of rows) {
      const parsed = DeploymentRequest.safeParse(row);
      if (!parsed.success) {
        throw new Error(
          `A live deployment row does not match DeploymentRequest:\n` +
            JSON.stringify(parsed.error.issues, null, 2),
        );
      }
    }
  });

  it("carries the states this screen exists to distinguish", () => {
    const statuses = new Set(rows.map((r) => (r as { approvalStatus: string }).approvalStatus));
    expect(statuses.has("pending")).toBe(true);
    expect(statuses.has("approved")).toBe(true);
  });

  it("keeps approval and execution as separate facts", () => {
    // The row that errored is still an APPROVED row. Collapsing the two would lose
    // the fact that a human said yes.
    const errored = rows.find(
      (r) => (r as { executionStatus: string }).executionStatus === "error",
    ) as { approvalStatus: string; approvedBy: string | null } | undefined;
    expect(errored).toBeTruthy();
    expect(errored!.approvalStatus).toBe("approved");
    expect(errored!.approvedBy).toBeTruthy();
  });
});

describe("the component rendering that payload", () => {
  async function show() {
    listDeployments.mockResolvedValue(rows);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <DeploymentApprovals projectId={"p1" as never} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listDeployments).toHaveBeenCalled());
  }

  it("renders without falling back to the error state", async () => {
    await show();
    expect(screen.queryByText(/Could not load deployments/)).toBeNull();
  });

  it("shows the pending request as awaiting approval", async () => {
    await show();
    await screen.findByText(/awaiting approval/);
  });

  it("does not present the errored deployment as succeeded", async () => {
    await show();
    expect(screen.queryByText(/Succeeded/)).toBeNull();
  });

  it("surfaces the real reason the deployment did not run", async () => {
    await show();
    await screen.findByText(/credentials are not configured/);
  });

  it("does not claim the failed deployment might have started", async () => {
    // The false alarm fixed in the executor: a missing credential fails before
    // anything is sent, so this warning must NOT appear for these rows.
    await show();
    expect(screen.queryByText(/It is not known whether this started/)).toBeNull();
  });

  it("shows what would run, for the request still awaiting a decision", async () => {
    await show();
    await screen.findByText(/pipeline 12/);
  });
});

describe("this contract test has teeth", () => {
  // A contract test that cannot fail is worse than none: it reports green while the
  // screen it guards is broken. These pin the two backend changes most likely to
  // happen, and prove the schema rejects both.
  const good = rows[0] as Record<string, unknown>;

  it("catches a renamed field", () => {
    const { approvalStatus, ...renamed } = good;
    expect(DeploymentRequest.safeParse({ ...renamed, approval_status: approvalStatus }).success)
      .toBe(false);
  });

  it("catches an execution status the UI has never heard of", () => {
    expect(DeploymentRequest.safeParse({ ...good, executionStatus: "rolling_back" }).success)
      .toBe(false);
  });

  it("catches an action the UI cannot describe", () => {
    expect(DeploymentRequest.safeParse({ ...good, action: "provision_resources" }).success)
      .toBe(false);
  });
});
