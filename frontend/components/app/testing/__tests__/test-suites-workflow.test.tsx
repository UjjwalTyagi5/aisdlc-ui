// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import type * as SuitesApi from "@/lib/api/testing-suites";

/**
 * The Testing flow: generate the unit, functional and API test cases for a branch, review,
 * download and raise each workbook, and run them — on a page that opens empty and shows one
 * piece of work at a time.
 */

const state = vi.hoisted(() => ({
  runs: [] as { doc: string; body: unknown }[],
  generated: [] as unknown[],
  history: [] as unknown[],
  suiteCalls: [] as string[],
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/api/testing-suites", async (orig) => {
  const actual = await orig<typeof SuitesApi>();
  return {
    ...actual,
    listHistory: async () => ({ entries: state.history, total: state.history.length }),
    runSuite: async (_p: string, doc: string, body: unknown) => {
      state.runs.push({ doc, body });
      return job({ id: "r1", kind: "run_unit", status: "queued" });
    },
    generateSuites: async (_p: string, body: unknown) => {
      state.generated.push(body);
      return job({ id: "g-new", kind: "generate", status: "queued" });
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
import type { HistoryEntry, SuiteJob } from "@/lib/api/testing-suites";

function job(over: Partial<SuiteJob> & { id: string; kind: string }): SuiteJob {
  return {
    project_id: "p", user_id: "u1", user_name: "Sarthak", status: "succeeded", created_at: "2026-09-17T12:00:00Z",
    started_at: null, finished_at: "2026-09-17T12:01:00Z", progress: [], error: "", params: {},
    result: { documents: [], failures: [] }, ...over,
  } as SuiteJob;
}

function doc(id: string, title: string, status = "draft") {
  return {
    id, projectId: "p", runId: "r", type: "document", scope: "agent", stage: "testing", title, status, version: 1,
    contentHash: "h".repeat(64), body: { kind: "document", filename: title, stored: true }, phase: "testing",
    createdBy: "agent", createdAt: "2026-09-17T12:00:00Z", updatedAt: "2026-09-17T12:00:00Z", approvedBy: null, approvedAt: null,
    downloadUrl: `/api/artifacts/${id}/download`,
  } as never;
}

const target = { ado_project: "QUICKLINK(Url shortner)", repo: "QUICKLINK(Url shortner)", branch: "feature/x" };

/** A generation that filed all three workbooks, with the given runs. */
function entry(runs: SuiteJob[] = [], genOver: Partial<SuiteJob> = {}): HistoryEntry {
  return {
    id: "g1", updated_at: "2026-09-17T12:30:00Z", active: false, runs,
    generation: job({
      id: "g1", kind: "generate", params: { target, kinds: ["unit", "functional", "api"] },
      result: { documents: [
        { kind: "unit", name: "QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx", url: "", artifact_id: "u1", cases: 15, skipped: [] },
        { kind: "functional", name: "QUICKLINK_Url_shortner_Functional_Test_Cases.xlsx", url: "", artifact_id: "f1", cases: 8, skipped: [] },
        { kind: "api", name: "QUICKLINK_Url_shortner_API_Test_Cases.xlsx", url: "", artifact_id: "a1", cases: 9, skipped: [] },
      ], failures: [] },
      ...genOver,
    }),
  };
}

const SUITES = () => [
  doc("u1", "QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx", "approved"),
  doc("f1", "QUICKLINK_Url_shortner_Functional_Test_Cases.xlsx"),
  doc("a1", "QUICKLINK_Url_shortner_API_Test_Cases.xlsx"),
];

const approvals = { mayRaise: () => true, raise: vi.fn(), raisingId: null };
const onOpenEntry = vi.fn();
const onShowHistory = vi.fn();

function renderFlow(props: Partial<React.ComponentProps<typeof TestSuitesWorkflow>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TestSuitesWorkflow projectId={"p" as never} target={target} onSelectTarget={() => {}} offeringId="off-grok"
        documents={[]} approvals={approvals} entry={null} onOpenEntry={onOpenEntry} onShowHistory={onShowHistory} {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  state.generated = []; state.history = []; state.suiteCalls = []; state.runs = [];
  approvals.raise.mockClear(); onOpenEntry.mockClear(); onShowHistory.mockClear();
  try { window.localStorage.clear(); } catch { /* no storage */ }
});
afterEach(cleanup);

describe("Testing flow — the page opens empty", () => {
  it("shows nothing from earlier work, even with test cases and reports on file", async () => {
    // LIVE: step 2 arrived filled with a 15-passed unit run from earlier, while step 1 said nothing was generated.
    state.history = [entry([job({ id: "r1", kind: "run_unit", params: { document_id: "u1", suite_kind: "unit" },
      result: { documents: [], failures: [], verdict: "Passed", totals: { Passed: 15 }, rows: [{ id: "UT-001", title: "a", status: "Passed" }] } })])];
    renderFlow({ documents: [...SUITES(), doc("rep", "QUICKLINK_Url_shortner_Unit_Test_Report.xlsx")] });
    const step1 = within(screen.getByRole("region", { name: "Test cases" }));
    expect(step1.getAllByText("Not generated yet.")).toHaveLength(3);
    expect(screen.queryByText("15 passed")).not.toBeInTheDocument();
    expect(screen.queryByText("UT-001")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run unit tests" })).toBeDisabled();
    expect(screen.queryByRole("region", { name: "Testing work in progress" })).not.toBeInTheDocument();
  });

  it("offers work still running from before instead of pouring it in", async () => {
    state.history = [{ ...entry([], { status: "running", finished_at: null, user_name: "Marcus" }), active: true }];
    renderFlow();
    expect(await screen.findByText(/Test cases are being generated for QUICKLINK\(Url shortner\) @ feature\/x — started by Marcus/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onOpenEntry).toHaveBeenCalledWith("g1");
  });

  it("generates the chosen suites for the branch with the page's model, and opens that work", async () => {
    renderFlow();
    fireEvent.click(screen.getByRole("checkbox", { name: "API test cases" }));
    fireEvent.click(screen.getByRole("button", { name: /Generate test cases/ }));
    await waitFor(() => expect(onOpenEntry).toHaveBeenCalledWith("g-new"));
    expect(state.generated).toEqual([{ target, kinds: ["unit", "functional"], offering_id: "off-grok" }]);
  });

  it("asks for a branch before anything can be generated", () => {
    renderFlow({ target: null });
    expect(screen.getByRole("button", { name: /Select a branch/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate test cases/ })).toBeDisabled();
  });
});

describe("Testing flow — step 1, the open work's test cases", () => {
  it("shows the work as it happens, and why it failed", () => {
    renderFlow({ entry: { ...entry([], {
      status: "failed", progress: [{ at: "", level: "info", message: "Checking out QUICKLINK @ feature/x" }],
      error: "git clone failed: repository not found", result: { documents: [], failures: [] },
    }) } });
    expect(screen.getByText("Checking out QUICKLINK @ feature/x")).toBeInTheDocument();
    expect(screen.getByText("git clone failed: repository not found")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/Test cases generated .* by Sarthak/)).toBeInTheDocument();
  });

  it("lists the workbooks this generation filed with approval, download, raise and cases", async () => {
    renderFlow({ entry: entry(), documents: [
      ...SUITES(),
      doc("f0", "QUICKLINK_Url_shortner_Functional_Test_Cases_v2.xlsx"), // a newer workbook of another generation
    ] });
    const step1 = within(screen.getByRole("region", { name: "Test cases" }));
    expect(step1.getByText("QUICKLINK_Url_shortner_Unit_Test_Cases.xlsx")).toBeInTheDocument();
    expect(step1.getByText("Approved")).toBeInTheDocument();
    expect(step1.queryByText("QUICKLINK_Url_shortner_Functional_Test_Cases_v2.xlsx")).not.toBeInTheDocument();
    expect(step1.getAllByRole("link", { name: /Excel/ }).map((a) => a.getAttribute("href")))
      .toEqual(["/api/artifacts/u1/download", "/api/artifacts/f1/download", "/api/artifacts/a1/download"]);

    fireEvent.click(step1.getAllByRole("button", { name: "Raise for approval" })[0]!);
    expect(approvals.raise).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getAllByRole("button", { name: /View cases/ })[1]!);
    expect(await screen.findByText("Create a short link")).toBeInTheDocument();
    expect(screen.getByText("Check the page shows “Link created successfully”")).toBeInTheDocument();
    expect(state.suiteCalls).toEqual(["f1"]);
  });

  it("says a workbook was removed from the project's documents and will not run it", () => {
    renderFlow({ entry: entry(), documents: [doc("f1", "QUICKLINK_Url_shortner_Functional_Test_Cases.xlsx")] });
    const step1 = within(screen.getByRole("region", { name: "Test cases" }));
    expect(step1.getAllByText("Removed from the project's documents.")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Run unit tests" })).toBeDisabled();
    expect(screen.getAllByText("These test cases were removed from the project's documents — generate them again.")).toHaveLength(2);
  });

  it("starts over, and shows History, from the open work", () => {
    renderFlow({ entry: entry(), documents: SUITES() });
    fireEvent.click(screen.getByRole("button", { name: "Start new" }));
    expect(onOpenEntry).toHaveBeenCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(onShowHistory).toHaveBeenCalledTimes(1);
  });
});

describe("Testing flow — steps 2 and 3, runs", () => {
  it("runs the open work's unit suite with the page's model", async () => {
    renderFlow({ entry: entry(), documents: SUITES() });
    fireEvent.click(screen.getByRole("button", { name: "Run unit tests" }));
    await waitFor(() => expect(state.runs).toEqual([{ doc: "u1", body: { offering_id: "off-grok" } }]));
  });

  it("needs the application's URL before a functional or API run, and sends it with the browser choice", async () => {
    renderFlow({ entry: entry(), documents: SUITES() });
    expect(screen.getByRole("button", { name: "Run functional tests" })).toBeDisabled();
    expect(screen.getAllByText("Enter the running application's URL above.")).toHaveLength(2);

    fireEvent.change(screen.getByLabelText("Application URL"), { target: { value: "http://localhost:8080" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Show the browser while functional tests run" }));
    fireEvent.click(screen.getByRole("button", { name: "Run functional tests" }));
    fireEvent.click(screen.getByRole("button", { name: "Run api tests" }));
    await waitFor(() => expect(state.runs).toHaveLength(2));
    expect(state.runs[0]).toEqual({ doc: "f1", body: { base_url: "http://localhost:8080", headless: true, offering_id: "off-grok" } });
    expect(state.runs[1]).toEqual({ doc: "a1", body: { base_url: "http://localhost:8080", offering_id: "off-grok" } });
  });

  it("shows the latest run's every case and the report it filed", () => {
    const older = job({ id: "r0", kind: "run_api", created_at: "2026-09-17T12:10:00Z", params: { document_id: "a1", suite_kind: "api" },
      result: { documents: [], failures: [], verdict: "Passed", totals: { Passed: 3 }, rows: [{ id: "AT-001", title: "Old", status: "Passed", message: "older run" }] } });
    const latest = job({ id: "r9", kind: "run_api", created_at: "2026-09-17T12:20:00Z", params: { document_id: "a1", suite_kind: "api" },
      result: { documents: [{ kind: "api", name: "QUICKLINK_Url_shortner_API_Test_Report.xlsx", url: "", artifact_id: "rep", skipped: [] }], failures: [],
        verdict: "Failed", totals: { Passed: 1, Failed: 1, Error: 0, "Not run": 1, total: 3 },
        commit: "debfe6f1185e", target_url: "http://localhost:8080", suite_document: "x", rows: [
          { id: "AT-001", title: "Create", subject: "POST /api/shorten", status: "Passed", message: "", evidence: "HTTP 201" },
          { id: "AT-002", title: "Stats", subject: "GET /api/links/{{code}}/stats", status: "Failed", message: "Expected status 200, got 500.", evidence: "HTTP 500" },
          { id: "AT-003", title: "Disable", subject: "POST /x", status: "Not run", message: "Not run: the application stopped responding during AT-002.", evidence: "" },
        ] } });
    renderFlow({ entry: entry([latest, older]), documents: [...SUITES(), doc("rep", "QUICKLINK_Url_shortner_API_Test_Report.xlsx")] });
    expect(screen.getByText("Expected status 200, got 500.")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    expect(screen.getByText("Not run: the application stopped responding during AT-002.")).toBeInTheDocument();
    expect(screen.getByText("QUICKLINK_Url_shortner_API_Test_Report.xlsx")).toBeInTheDocument();
    expect(screen.queryByText("older run")).not.toBeInTheDocument();
  });

  it("says when a run's report was removed from the project's documents", () => {
    const run = job({ id: "r1", kind: "run_unit", params: { document_id: "u1", suite_kind: "unit" },
      result: { documents: [{ kind: "unit", name: "QUICKLINK_Url_shortner_Unit_Test_Report.xlsx", url: "", artifact_id: "gone", skipped: [] }], failures: [],
        verdict: "Passed", totals: { Passed: 1 }, rows: [{ id: "UT-001", title: "a", status: "Passed" }] } });
    renderFlow({ entry: entry([run]), documents: SUITES() });
    expect(screen.getByText(/was removed from the project's documents/)).toHaveTextContent("QUICKLINK_Url_shortner_Unit_Test_Report.xlsx");
  });
});
