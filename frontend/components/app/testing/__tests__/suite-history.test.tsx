// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import type * as SuitesApi from "@/lib/api/testing-suites";

/** The Testing page's History: what was generated and run before, to open again. */

const state = vi.hoisted(() => ({ pages: [] as { entries: unknown[]; total: number }[], calls: [] as unknown[] }));

vi.mock("@/lib/api/testing-suites", async (orig) => {
  const actual = await orig<typeof SuitesApi>();
  return {
    ...actual,
    listHistory: async (_p: string, page: { offset?: number }) => {
      state.calls.push(page);
      return state.pages[Math.floor((page.offset ?? 0) / 20)] ?? { entries: [], total: 0 };
    },
  };
});

import { SuiteHistory } from "@/components/app/testing/suite-history";
import type { HistoryEntry, SuiteJob } from "@/lib/api/testing-suites";

function job(over: Partial<SuiteJob> & { id: string; kind: string }): SuiteJob {
  return {
    project_id: "p", user_id: "u1", user_name: "Sarthak", status: "succeeded", created_at: "2026-09-17T12:00:00Z",
    started_at: null, finished_at: "2026-09-17T12:01:00Z", progress: [], error: "", params: {},
    result: { documents: [], failures: [] }, ...over,
  } as SuiteJob;
}

function doc(id: string, title: string, status = "draft") {
  return { id, projectId: "p", stage: "testing", title, status, createdAt: "2026-09-17T12:00:00Z", downloadUrl: `/api/artifacts/${id}/download` } as never;
}

const target = { ado_project: "Q", repo: "QUICKLINK", branch: "feature/x" };

function gen(id: string, branch: string, docs: [string, string, number][], over: Partial<SuiteJob> = {}) {
  return job({ id, kind: "generate", params: { target: { ...target, branch }, kinds: docs.map(([k]) => k) },
    result: { documents: docs.map(([kind, artifact_id, cases]) => ({ kind: kind as never, name: `${kind}.xlsx`, url: "", artifact_id, cases, skipped: [] })), failures: [] },
    ...over });
}

const ENTRIES: HistoryEntry[] = [
  {
    id: "g2", updated_at: "2026-09-17T12:30:00Z", active: false,
    generation: gen("g2", "feature/x", [["unit", "u2", 15], ["api", "a2", 9]]),
    runs: [
      job({ id: "r2", kind: "run_api", user_name: "Marcus", params: { document_id: "a2", suite_kind: "api", base_url: "http://localhost:8080" },
        result: { documents: [{ kind: "api", name: "API_Report.xlsx", url: "", artifact_id: "rep-a", skipped: [] }], failures: [],
          verdict: "Failed", totals: { Passed: 8, Failed: 1 }, rows: Array.from({ length: 9 }, (_, i) => ({ id: `AT-00${i}`, title: "x", status: "Passed" })) } }),
      job({ id: "r1", kind: "run_unit", params: { document_id: "u2", suite_kind: "unit" },
        result: { documents: [{ kind: "unit", name: "Unit_Report.xlsx", url: "", artifact_id: "rep-gone", skipped: [] }], failures: [],
          verdict: "Passed", totals: { Passed: 15 }, rows: Array.from({ length: 15 }, (_, i) => ({ id: `UT-0${i}`, title: "x", status: "Passed" })) } }),
    ],
  },
  { id: "g1", updated_at: "2026-09-16T10:00:00Z", active: false, runs: [],
    generation: gen("g1", "main", [], { status: "failed", error: "git clone failed: repository not found" }) },
];

const onOpen = vi.fn();

function renderHistory(props: Partial<React.ComponentProps<typeof SuiteHistory>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SuiteHistory projectId={"p" as never} documents={[doc("u2", "unit.xlsx", "approved"), doc("rep-a", "API_Report.xlsx")]}
        openEntryId={null} onOpen={onOpen} {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => { state.pages = []; state.calls = []; onOpen.mockClear(); });
afterEach(cleanup);

describe("Testing history", () => {
  it("lists each generation with who made it, its test cases and every run", async () => {
    state.pages = [{ entries: ENTRIES, total: 2 }];
    renderHistory();
    const item = within(await screen.findByRole("listitem", { name: "QUICKLINK @ feature/x" }));
    expect(item.getByText(/Generated .* by Sarthak/)).toBeInTheDocument();
    const cases = within(item.getByLabelText("Test cases"));
    expect(cases.getByText("15 cases")).toBeInTheDocument();
    expect(cases.getByText("Approved")).toBeInTheDocument();
    expect(cases.getByText("Removed")).toBeInTheDocument(); // a2 is no longer in the project's documents

    const runs = within(item.getByRole("list", { name: "Runs" })).getAllByRole("listitem");
    expect(runs[0]).toHaveTextContent("API tests");
    expect(runs[0]).toHaveTextContent("8 of 9 passed · 1 failed");
    expect(runs[0]).toHaveTextContent("Marcus · http://localhost:8080");
    expect(within(runs[0]!).getByRole("link", { name: /Report/ })).toHaveAttribute("href", "/api/artifacts/rep-a/download");
    expect(runs[1]).toHaveTextContent("15 of 15 passed");
    expect(runs[1]).toHaveTextContent("Report removed");

    const failed = within(screen.getByRole("listitem", { name: "QUICKLINK @ main" }));
    expect(failed.getByText("git clone failed: repository not found")).toBeInTheDocument();
    expect(failed.getByText("Failed")).toBeInTheDocument();
  });

  it("opens an entry on the page, and marks the one already open", async () => {
    state.pages = [{ entries: ENTRIES, total: 2 }];
    renderHistory({ openEntryId: "g1" });
    const item = within(await screen.findByRole("listitem", { name: "QUICKLINK @ feature/x" }));
    fireEvent.click(item.getByRole("button", { name: "Open" }));
    expect(onOpen).toHaveBeenCalledWith("g2");
    expect(within(screen.getByRole("listitem", { name: "QUICKLINK @ main" })).getByText("Open on the page")).toBeInTheDocument();
  });

  it("pages through a long history", async () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ ...ENTRIES[1]!, id: `e${i}` }));
    state.pages = [{ entries: many, total: 21 }, { entries: [{ ...ENTRIES[0]!, id: "last" }], total: 21 }];
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("listitem", { name: "QUICKLINK @ feature/x" })).toBeInTheDocument();
    expect(state.calls).toEqual([{ limit: 20, offset: 0 }, { limit: 20, offset: 20 }]);
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  });

  it("says when there is nothing yet", async () => {
    state.pages = [{ entries: [], total: 0 }];
    renderHistory();
    expect(await screen.findByText("No testing history yet")).toBeInTheDocument();
  });
});
