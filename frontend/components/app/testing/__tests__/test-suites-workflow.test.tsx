// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import type * as SuitesApi from "@/lib/api/testing-suites";

/**
 * Step 1 of the Testing flow: generate the unit, functional and API test cases for a branch,
 * follow the work, and review, download and raise each workbook.
 */

const state = vi.hoisted(() => ({
  runs: [] as { doc: string; body: unknown }[],
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
    runSuite: async (_p: string, doc: string, body: unknown) => {
      state.runs.push({ doc, body });
      return { id: "r1", kind: "run_unit", project_id: "p", status: "queued", created_at: new Date().toISOString(), progress: [], result: { documents: [], failures: [] }, error: "", params: {} };
    },
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

beforeEach(() => {
  state.generated = []; state.jobs = []; state.suiteCalls = []; state.runs = []; approvals.raise.mockClear();
  try { window.localStorage.clear(); } catch { /* no storage */ }
});
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
    const step1 = within(screen.getByRole("region", { name: "Test cases" }));
    expect(step1.getByText("QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx")).toBeInTheDocument();
    expect(step1.getByText("Approved")).toBeInTheDocument();
    expect(step1.getAllByText("Not generated yet.")).toHaveLength(1);
    expect(step1.getAllByRole("link", { name: /Excel/ }).map((a) => a.getAttribute("href")))
      .toEqual(["/api/artifacts/u1/download", "/api/artifacts/f2/download"]);

    fireEvent.click(step1.getByRole("button", { name: "Raise for approval" }));
    expect(approvals.raise).toHaveBeenCalledTimes(1);

    const view = screen.getAllByRole("button", { name: /View cases/ });
    fireEvent.click(view[1]!);
    expect(await screen.findByText("Create a short link")).toBeInTheDocument();
    expect(screen.getByText("Check the page shows “Link created successfully”")).toBeInTheDocument();
    expect(state.suiteCalls).toEqual(["f2"]);
  });
});

const SUITES = () => [
  doc("u1", "QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx", "approved"),
  doc("f1", "QUICKLINK_Url_shortner_Functional_Test_Cases.xlsx"),
  doc("a1", "QUICKLINK_Url_shortner_API_Test_Cases.xlsx"),
];

describe("Testing flow — steps 2 and 3, runs", () => {
  it("runs the unit suite on file with the page's model", async () => {
    renderFlow({ documents: SUITES() });
    fireEvent.click(screen.getByRole("button", { name: "Run unit tests" }));
    await waitFor(() => expect(state.runs).toEqual([{ doc: "u1", body: { offering_id: "off-grok" } }]));
  });

  it("needs the application's URL before a functional or API run, and sends it with the browser choice", async () => {
    renderFlow({ documents: SUITES() });
    const functional = screen.getByRole("button", { name: "Run functional tests" });
    expect(functional).toBeDisabled();
    expect(screen.getAllByText("Enter the running application's URL above.")).toHaveLength(2);

    fireEvent.change(screen.getByLabelText("Application URL"), { target: { value: "http://localhost:8080" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Show the browser while functional tests run" }));
    fireEvent.click(screen.getByRole("button", { name: "Run functional tests" }));
    fireEvent.click(screen.getByRole("button", { name: "Run api tests" }));
    await waitFor(() => expect(state.runs).toHaveLength(2));
    expect(state.runs[0]).toEqual({ doc: "f1", body: { base_url: "http://localhost:8080", headless: true, offering_id: "off-grok" } });
    expect(state.runs[1]).toEqual({ doc: "a1", body: { base_url: "http://localhost:8080", offering_id: "off-grok" } });
  });

  it("shows every case's outcome and the filed report after a run", async () => {
    state.jobs = [{
      id: "r9", kind: "run_api", project_id: "p", status: "succeeded", created_at: new Date().toISOString(),
      finished_at: new Date().toISOString(), progress: [], error: "", params: {},
      result: { documents: [], failures: [], verdict: "Failed", totals: { Passed: 1, Failed: 1, Error: 0, "Not run": 1, total: 3 },
        commit: "debfe6f1185e", target_url: "http://localhost:8080", suite_document: "x", rows: [
          { id: "AT-001", title: "Create", subject: "POST /api/shorten", status: "Passed", message: "", evidence: "HTTP 201" },
          { id: "AT-002", title: "Stats", subject: "GET /api/links/{{code}}/stats", status: "Failed", message: "Expected status 200, got 500.", evidence: "HTTP 500" },
          { id: "AT-003", title: "Disable", subject: "POST /x", status: "Not run", message: "Not run: the application stopped responding during AT-002.", evidence: "" },
        ] },
    }];
    renderFlow({ documents: [...SUITES(), doc("rep", "QUICKLINK_Url_shortner_API_Test_Report.xlsx")] });
    expect(await screen.findByText("Expected status 200, got 500.")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    expect(screen.getByText("Not run: the application stopped responding during AT-002.")).toBeInTheDocument();
    expect(screen.getByText("QUICKLINK_Url_shortner_API_Test_Report.xlsx")).toBeInTheDocument();
  });
});
