// @vitest-environment jsdom
/**
 * The Testing agent's run as a report: the verdict is the title, the numbers are
 * the run's own, and a generated suite that could not run is said in red rather than
 * hidden behind "Passed 2/2".
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TestRunReport } from "@/components/app/test-run-report";
import { RunReport, type RunReport as RunReportT } from "@/lib/api/testing";

afterEach(cleanup);

const BASE = {
  sessionId: "s1",
  verdict: "passed",
  testTypes: ["unit"],
  language: "react",
  framework: "jest",
  runnerCommand: "npx jest --coverage",
  target: { project: "QuickLink", repo: "QuickLink", branch: "main" },
  execution: { total: 2, passed: 2, failed: 0, skipped: 0, errors: 0, durationMs: 1840 },
  coverage: {
    linePct: 45.2, branchPct: 30, statements: 84, missed: 46, applicationPct: 36.8, thresholdPct: 80,
    files: [
      { path: "src/views/index.ejs", pct: 0, statements: 10, covered: 0, missed: 10, bucket: "Views" },
      { path: "src/services/LinkService.js", pct: 61, statements: 40, covered: 24, missed: 16, bucket: "Application source" },
    ],
  },
  tests: [
    { name: "creates a short link", suite: "LinkService", status: "passed", durationMs: 12, message: "" },
    { name: "redirects", suite: "routes", status: "passed", durationMs: 8, message: "" },
  ],
  testCases: [
    { id: "TC-01", feature: "createLink", summary: "Shortens a valid URL", scenarioType: "Happy Path", steps: "1. POST /links 2. read slug", data: "https://a.b/c", expected: "7-char slug" },
    { id: "TC-02", feature: "createLink", summary: "Rejects an invalid URL", scenarioType: "Error Case", steps: "POST /links with 'x'", data: "x", expected: "400" },
  ],
  defects: [],
  unrunnableSuites: [],
  generatedFiles: ["src/generated_unit.test.js"],
  artifactFiles: ["results.xml", "coverage_report.xml"],
  qaReportAvailable: true,
  lintExit: 0,
  summaryMd: "## Testing completed",
} satisfies RunReportT;

function report(over: Partial<RunReportT> = {}): RunReportT {
  return RunReport.parse({ ...BASE, ...over });
}

describe("TestRunReport", () => {
  it("leads with the verdict and the run's numbers", () => {
    render(<TestRunReport report={report()} />);
    expect(screen.getByRole("heading", { level: 2, name: "2 of 2 tests passed" })).toBeInTheDocument();
    expect(screen.getByText(/Unit · QuickLink @ main/)).toBeInTheDocument();
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    // the line figure sits in the facts strip AND over the coverage bar
    expect(screen.getAllByText("45.2%")).toHaveLength(2);
    expect(screen.getByText("36.8%")).toBeInTheDocument();
    expect(screen.getByText("1.8 s")).toBeInTheDocument();
    expect(screen.getByText("threshold 80%", { selector: "dd" })).toBeInTheDocument();
  });

  it("numbers its sections in order and lists every test, file and designed case", () => {
    render(<TestRunReport report={report()} />);
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Results", "Coverage", "Test cases", "Defects", "Run details"]);
    expect(screen.getByText("creates a short link")).toBeInTheDocument();
    expect(screen.getByText("src/views/index.ejs")).toBeInTheDocument();
    expect(screen.getByText("Shortens a valid URL")).toBeInTheDocument();
    expect(screen.getByText("Happy Path")).toBeInTheDocument();
    expect(screen.getByText("Error Case")).toBeInTheDocument();
    expect(screen.getByText("No defects were raised by this run.")).toBeInTheDocument();
    expect(screen.getByText("src/generated_unit.test.js")).toBeInTheDocument();
  });

  it("says when a generated suite could not run, and counts it as a defect", () => {
    render(
      <TestRunReport
        report={report({
          verdict: "partial",
          unrunnableSuites: [{ file: "src/generated_unit.test.jsx", reason: "Jest encountered an unexpected token" }],
          defects: [{ id: "DEF-SUITE-001", severity: "high", summary: "Generated test suite could not run: generated_unit.test.jsx", detail: "Jest encountered an unexpected token" }],
        })}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: /1 generated suite could not run/ })).toBeInTheDocument();
    expect(screen.getByText("Generated tests did not run")).toBeInTheDocument();
    expect(screen.getByText("DEF-SUITE-001")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
  });

  it("puts failures first and shows their message", () => {
    render(
      <TestRunReport
        report={report({
          verdict: "failed",
          execution: { total: 2, passed: 1, failed: 1, skipped: 0, errors: 0, durationMs: 100 },
          tests: [
            { name: "passes", suite: "a", status: "passed", durationMs: 1, message: "" },
            { name: "breaks", suite: "a", status: "failed", durationMs: 1, message: "expected 409 got 200" },
          ],
        })}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "1 of 2 tests failed" })).toBeInTheDocument();
    const rows = screen.getAllByRole("row").map((r) => r.textContent ?? "");
    expect(rows.findIndex((r) => r.includes("breaks"))).toBeLessThan(rows.findIndex((r) => r.includes("passes")));
    expect(screen.getByText("expected 409 got 200")).toBeInTheDocument();
  });

  it("renders a run that produced nothing without pretending", () => {
    render(<TestRunReport report={report({ verdict: "error", execution: { total: 0, passed: 0, failed: 0, skipped: 0, errors: 0, durationMs: 0 }, tests: [], coverage: { linePct: 0, statements: 0, missed: 0, files: [] }, testCases: [] })} />);
    expect(screen.getByRole("heading", { level: 2, name: "The run did not complete" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "Coverage" })).not.toBeInTheDocument();
  });
});
