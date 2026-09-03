// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The deployment approval queue.
 *
 * THE ONE THING THIS SCREEN MUST NOT DO is make a deployment look further along than it
 * is. The dangerous state is `approved` with nothing run: the decision was taken, the
 * deployment has not happened, and a green tick there is a lie the eye believes before
 * anyone reads the text. Most of these tests are about that distinction and the ones
 * either side of it.
 */

const listDeployments = vi.fn();
const approveDeployment = vi.fn();
const rejectDeployment = vi.fn();
const executeDeployment = vi.fn();
const refreshDeployment = vi.fn();

vi.mock("@/lib/api/deployment", () => ({
  listDeployments: (...a: unknown[]) => listDeployments(...a),
  approveDeployment: (...a: unknown[]) => approveDeployment(...a),
  rejectDeployment: (...a: unknown[]) => rejectDeployment(...a),
  executeDeployment: (...a: unknown[]) => executeDeployment(...a),
  refreshDeployment: (...a: unknown[]) => refreshDeployment(...a),
}));

// The UI capability check is not what this file is about — the backend enforces the
// real permission. Render the children so the buttons are assertable.
vi.mock("@/components/auth/require-role", () => ({
  RequireRole: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { DeploymentApprovals } from "@/components/app/deployment-approvals";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

type Dep = Record<string, unknown>;

const dep = (over: Dep = {}): Dep => ({
  id: "d1",
  projectId: "p1",
  runId: null,
  action: "run_pipeline",
  targetKind: "azure_pipelines",
  environment: "prod",
  request: { pipeline_id: 12, branch: "main" },
  requestedBy: "alice",
  requestedAt: "2026-09-03T10:00:00Z",
  approvalStatus: "pending",
  approvedBy: null,
  approvedAt: null,
  rejectionReason: null,
  executionStatus: "not_started",
  executedAt: null,
  externalId: null,
  externalUrl: null,
  outcome: null,
  ...over,
});

async function show(rows: Dep[]) {
  listDeployments.mockResolvedValue(rows);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <DeploymentApprovals projectId={"p1" as never} />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(listDeployments).toHaveBeenCalled());
}

describe("an approved deployment that has not run", () => {
  it("does not present itself as done", async () => {
    await show([dep({ approvalStatus: "approved", approvedBy: "bob",
                      executionStatus: "not_started" })]);
    await screen.findByText(/Approved — not yet run/);
    expect(screen.queryByText(/Succeeded/)).toBeNull();
  });

  it("says in words that nothing has run", async () => {
    await show([dep({ approvalStatus: "approved", approvedBy: "bob" })]);
    await screen.findByText(/Approved, but nothing has run yet/);
  });

  it("offers the button that actually deploys", async () => {
    await show([dep({ approvalStatus: "approved", approvedBy: "bob" })]);
    await screen.findByRole("button", { name: /Deploy now/ });
  });
});

describe("a deployment waiting on somebody", () => {
  it("says how many are waiting and that nothing has run", async () => {
    await show([dep(), dep({ id: "d2" })]);
    await screen.findByText(/2 deployments awaiting approval/);
    await screen.findByText(/Nothing has run/);
  });

  it("says the requester cannot approve their own", async () => {
    await show([dep()]);
    await screen.findByText(/cannot be approved by the person who raised it/);
  });

  it("offers approve and reject, and no deploy button", async () => {
    await show([dep()]);
    await screen.findByRole("button", { name: /Approve/ });
    await screen.findByRole("button", { name: /Reject/ });
    expect(screen.queryByRole("button", { name: /Deploy now/ })).toBeNull();
  });

  it("shows what would actually run, so there is something to approve", async () => {
    await show([dep()]);
    await screen.findByText(/pipeline 12/);
    await screen.findByText(/branch main/);
  });
});

describe("a running deployment", () => {
  it("is not reported as a success", async () => {
    await show([dep({ approvalStatus: "approved", approvedBy: "bob",
                      executionStatus: "running" })]);
    await screen.findByText("Running");
    expect(screen.queryByText(/Succeeded/)).toBeNull();
  });

  it("can be checked rather than left to guess", async () => {
    await show([dep({ approvalStatus: "approved", approvedBy: "bob",
                      executionStatus: "running" })]);
    await screen.findByRole("button", { name: /Check status/ });
  });
});

describe("a deployment that failed", () => {
  it("names the stage that broke", async () => {
    await show([dep({
      approvalStatus: "approved", approvedBy: "bob", executionStatus: "failed",
      outcome: { failed_stages: [{ name: "Deploy to prod", result: "failed",
                                   issues: [{ message: "image pull backoff" }] }] },
    })]);
    await screen.findByText(/Failed at: Deploy to prod/);
  });

  it("shows the error the pipeline gave", async () => {
    await show([dep({
      approvalStatus: "approved", approvedBy: "bob", executionStatus: "failed",
      outcome: { failed_stages: [{ name: "Deploy", issues: [{ message: "image pull backoff" }] }] },
    })]);
    await screen.findByText(/image pull backoff/);
  });
});

describe("a deployment whose outcome is genuinely unknown", () => {
  it("says so rather than picking failed or succeeded", async () => {
    await show([dep({
      approvalStatus: "approved", approvedBy: "bob", executionStatus: "error",
      outcome: { started_unknown: true, detail: "The Azure DevOps call failed." },
    })]);
    await screen.findByText(/It is not known whether this started/);
  });

  it("warns against redeploying on top of it", async () => {
    await show([dep({
      approvalStatus: "approved", approvedBy: "bob", executionStatus: "error",
      outcome: { started_unknown: true },
    })]);
    await screen.findByText(/Check Azure DevOps before retrying/);
  });
});

describe("refusals from the gate", () => {
  it("shows the reason the backend gave, not a generic failure", async () => {
    approveDeployment.mockRejectedValue(
      new Error("You cannot approve your own deployment request."));
    await show([dep()]);
    (await screen.findByRole("button", { name: /Approve/ })).click();
    await screen.findByText(/cannot approve your own deployment request/);
  });
});

describe("an empty queue", () => {
  it("explains that only what reaches an environment needs approval", async () => {
    await show([]);
    await screen.findByText(/only what reaches an environment does/);
  });

  it("does not claim anything is awaiting approval", async () => {
    await show([]);
    expect(screen.queryByText(/awaiting approval/)).toBeNull();
  });
});

describe("a rejected deployment", () => {
  it("is shown as rejected with its reason", async () => {
    await show([dep({ approvalStatus: "rejected", approvedBy: "bob",
                      rejectionReason: "failing security gate" })]);
    await screen.findByText("Rejected");
    await screen.findByText(/failing security gate/);
  });

  it("offers no way to run it", async () => {
    await show([dep({ approvalStatus: "rejected", approvedBy: "bob" })]);
    expect(screen.queryByRole("button", { name: /Deploy now/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });
});
