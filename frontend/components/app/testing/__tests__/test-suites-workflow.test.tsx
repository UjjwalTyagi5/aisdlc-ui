// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import type * as SuitesApi from "@/lib/api/testing-suites";

/**
 * Step 1 of the Testing flow: generate the unit, functional and API test cases for a branch,
 * follow the work, and review, download and raise each workbook.
 */

const state = vi.hoisted(() => ({
  generated: [] as unknown[],
  jobs: [] as unknown[],
  suiteCalls: [] as string[],
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/api/testing-suites", async (orig) => {
  const actual = await orig<typeof SuitesApi>();
  return {
    ...actual,
    listSuiteJobs: async () => ({ jobs: state.jobs }),
    generateSuites: async (_p: string, body: unknown) => {
      state.generated.push(body);
      return { id: "j2", kind: "generate", project_id: "p", status: "queued", created_at: new Date().toISOString(), progress: [], result: { documents: [], failures: [] }, error: "", params: {} };
    },
    getSuite: async (_p: string, doc: string) => {
      state.suiteCalls.push(doc);
      return {
        documentId: doc, status: "draft", problems: [],
        meta: { kind: "functional", project: "Url Shortner 1", source_project: "Q", repository: "QUICKLINK", branch: "feature/x", commit: "082f91e49bf0", generated_at: "17 Sep 2026", sources: "BRD", app_notes: "" },
        cases: [{ id: "FT-001", title: "Create a short link", requirement: "FR-1", priority: "High", preconditions: "", expected: "Link created",
          steps: [{ action: "open", target: "/", value: "" }, { action: "assert_text", target: "", value: "Link created successfully" }] }],
      };
    },
  };
});

import { TestSuitesWorkflow } from "@/components/app/testing/test-suites-workflow";

function doc(id: string, title: string, status = "draft") {
  return {
    id, projectId: "p", runId: "r", type: "document", scope: "agent", stage: "testing", title, status, version: 1,
    contentHash: "h".repeat(64), body: { kind: "document", filename: title, stored: true }, phase: "testing",
    createdBy: "agent", createdAt: "2026-09-17T12:00:00Z", updatedAt: "2026-09-17T12:00:00Z", approvedBy: null, approvedAt: null,
    downloadUrl: `/api/artifacts/${id}/download`,
  } as never;
}

const approvals = { mayRaise: () => true, raise: vi.fn(), raisingId: null };
const target = { ado_project: "QUICKLINK(Url shortner)", repo: "QUICKLINK(Url shortner)", branch: "feature/x" };

function renderFlow(props: Partial<React.ComponentProps<typeof TestSuitesWorkflow>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TestSuitesWorkflow projectId={"p" as never} target={target} onSelectTarget={() => {}} offeringId="off-grok"
        documents={[]} approvals={approvals} {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => { state.generated = []; state.jobs = []; state.suiteCalls = []; approvals.raise.mockClear(); });
afterEach(cleanup);

describe("Testing flow — step 1, test cases", () => {
  it("generates the chosen suites for the branch with the page's model", async () => {
    renderFlow();
    fireEvent.click(screen.getByRole("checkbox", { name: "API test cases" }));
    fireEvent.click(screen.getByRole("button", { name: /Generate test cases/ }));
    await waitFor(() => expect(state.generated).toHaveLength(1));
    expect(state.generated[0]).toEqual({ target, kinds: ["unit", "functional"], offering_id: "off-grok" });
  });

  it("asks for a branch before anything can be generated", () => {
    renderFlow({ target: null });
    expect(screen.getByRole("button", { name: /Select a branch/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate test cases/ })).toBeDisabled();
  });

  it("shows the work as it happens, and why it failed", async () => {
    state.jobs = [{
      id: "j1", kind: "generate", project_id: "p", status: "failed", created_at: new Date().toISOString(),
      finished_at: new Date().toISOString(), progress: [{ at: "", level: "info", message: "Checking out QUICKLINK @ feature/x" }],
      result: { documents: [], failures: [] }, error: "git clone failed: repository not found", params: {},
    }];
    renderFlow();
    expect(await screen.findByText("Checking out QUICKLINK @ feature/x")).toBeInTheDocument();
    expect(screen.getByText("git clone failed: repository not found")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("lists the newest workbook of each kind with its approval, download, raise and cases", async () => {
    renderFlow({ documents: [
      doc("u1", "QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx", "approved"),
      doc("f2", "QUICKLINK_Url_shortner_Functional_Test_Cases_v2.xlsx"),
      doc("f1", "QUICKLINK_Url_shortner_Functional_Test_Cases.xlsx"),
      doc("brd", "Url_Shortner_1_BRD_v2.docx"),
    ] });
    expect(screen.getByText("QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getAllByText("Not generated yet.")).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: /Excel/ }).map((a) => a.getAttribute("href")))
      .toEqual(["/api/artifacts/u1/download", "/api/artifacts/f2/download"]);

    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    expect(approvals.raise).toHaveBeenCalledTimes(1);

    const view = screen.getAllByRole("button", { name: /View cases/ });
    fireEvent.click(view[1]!);
    expect(await screen.findByText("Create a short link")).toBeInTheDocument();
    expect(screen.getByText("Check the page shows “Link created successfully”")).toBeInTheDocument();
    expect(state.suiteCalls).toEqual(["f2"]);
  });
});
