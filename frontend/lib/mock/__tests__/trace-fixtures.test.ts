import { describe, expect, it } from "vitest";

import { Trace, TraceMetrics } from "@/lib/schemas";
import { TRACES, buildMetrics, percentile, traceForRun } from "@/lib/mock/trace-fixtures";

describe("trace fixtures", () => {
  it("every fixture conforms to the Trace schema", () => {
    for (const t of TRACES) expect(() => Trace.parse(t)).not.toThrow();
  });

  it("metrics envelope conforms and is deterministic", () => {
    const m = buildMetrics(30);
    expect(() => TraceMetrics.parse(m)).not.toThrow();
    expect(m.totalTraces).toBe(TRACES.length);
    expect(buildMetrics(30)).toEqual(m); // pure / stable
  });

  it("percentile is monotonic", () => {
    const v = [10, 20, 30, 40, 50];
    expect(percentile(v, 50)).toBeLessThanOrEqual(percentile(v, 95));
  });

  it("traceForRun resolves a known run", () => {
    expect(traceForRun("run_2140")?.runId).toBe("run_2140");
  });
});
