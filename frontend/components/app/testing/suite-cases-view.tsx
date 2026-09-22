"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { LoadingState } from "@/components/ui/loading-state";
import { Callout, Pill, ReportTable, td, th, type PillTone } from "@/components/app/report-primitives";
import { ApiRequestError } from "@/lib/api/client";
import { getSuite, suitesKeys, type SuiteCase, type SuiteKind } from "@/lib/api/testing-suites";
import type { ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const PRIORITY_TONE: Record<string, PillTone> = { High: "danger", Medium: "warning", Low: "neutral" };

function stepText(s: { action: string; target: string; value: string }): string {
  switch (s.action) {
    case "open": return `Open ${s.target || "/"}`;
    case "click": return `Click “${s.target}”`;
    case "type": return `Type “${s.value}” into “${s.target}”`;
    case "select": return `Select “${s.value}” in “${s.target}”`;
    case "check": return `Tick “${s.target}”`;
    case "wait": return `Wait ${s.value || "1"} s`;
    case "assert_text": return `Check the page shows “${s.value}”`;
    case "assert_not_text": return `Check the page does not show “${s.value}”`;
    case "assert_url": return `Check the address contains “${s.value}”`;
    default: return `${s.action} ${s.target} ${s.value}`.trim();
  }
}

/** The cases a stored suite workbook holds — read back exactly as a run would read them. */
export function SuiteCasesView({ projectId, documentId, kind }: { projectId: ProjectId; documentId: string; kind: SuiteKind }) {
  const q = useQuery({
    queryKey: suitesKeys.suite(projectId, documentId),
    queryFn: () => getSuite(projectId, documentId),
    staleTime: 60_000,
    retry: false,
  });
  if (q.isLoading) return <LoadingState variant="card" />;
  if (q.isError || !q.data) {
    return (
      <Callout tone="danger" title="These test cases could not be read">
        {q.error instanceof ApiRequestError || q.error instanceof Error ? q.error.message : "Unknown error."}
      </Callout>
    );
  }
  const { meta, cases, problems } = q.data;
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-xs">
        {cases.length} case{cases.length === 1 ? "" : "s"} · {meta.repository} @ {meta.branch}
        {meta.commit ? ` (${meta.commit.slice(0, 7)})` : ""} · generated {meta.generated_at || "—"}
        {meta.sources ? ` · from ${meta.sources}` : ""}
      </p>
      {problems.length > 0 && (
        <Callout tone="warning" title={`${problems.length} row(s) in the workbook cannot run`}>
          <ul className="list-disc pl-4">{problems.slice(0, 8).map((p) => <li key={p}>{p}</li>)}</ul>
        </Callout>
      )}
      {kind === "unit" ? (
        <ReportTable dense head={<><th className={cn(th, "w-20")}>ID</th><th className={th}>Test case</th><th className={th}>Under test</th><th className={cn(th, "w-24")}>Scenario</th><th className={th}>Expected result</th><th className={cn(th, "w-20")}>Priority</th></>}>
          {cases.map((c: SuiteCase) => (
            <tr key={c.id}>
              <td className={cn(td, "font-mono text-[11px] font-semibold")}>{c.id}</td>
              <td className={td}><p className="font-medium">{c.title}</p>{c.input && <p className="text-muted-foreground text-xs">Input: {c.input}</p>}</td>
              <td className={cn(td, "font-mono text-[11px]")}>{c.module}{c.function ? ` · ${c.function}` : ""}</td>
              <td className={td}>{c.scenario}</td>
              <td className={cn(td, "text-muted-foreground")}>{c.expected}</td>
              <td className={td}><Pill tone={PRIORITY_TONE[c.priority] ?? "neutral"}>{c.priority}</Pill></td>
            </tr>
          ))}
        </ReportTable>
      ) : kind === "functional" ? (
        <ReportTable dense head={<><th className={cn(th, "w-20")}>ID</th><th className={th}>Test case</th><th className={th}>Steps</th><th className={th}>Expected result</th><th className={cn(th, "w-20")}>Priority</th></>}>
          {cases.map((c: SuiteCase) => (
            <tr key={c.id}>
              <td className={cn(td, "font-mono text-[11px] font-semibold")}>{c.id}</td>
              <td className={td}><p className="font-medium">{c.title}</p>{c.preconditions && <p className="text-muted-foreground text-xs">Before: {c.preconditions}</p>}</td>
              <td className={td}>
                <ol className="list-decimal space-y-0.5 pl-4 text-xs">
                  {(c.steps ?? []).map((s, i) => <li key={i} className={s.action.startsWith("assert_") ? "font-medium" : undefined}>{stepText(s)}</li>)}
                </ol>
              </td>
              <td className={cn(td, "text-muted-foreground")}>{c.expected}</td>
              <td className={td}><Pill tone={PRIORITY_TONE[c.priority] ?? "neutral"}>{c.priority}</Pill></td>
            </tr>
          ))}
        </ReportTable>
      ) : (
        <ReportTable dense head={<><th className={cn(th, "w-20")}>ID</th><th className={th}>Test case</th><th className={th}>Request</th><th className={cn(th, "w-20")}>Expects</th><th className={th}>Response must contain</th><th className={cn(th, "w-20")}>Priority</th></>}>
          {cases.map((c: SuiteCase) => (
            <tr key={c.id}>
              <td className={cn(td, "font-mono text-[11px] font-semibold")}>{c.id}</td>
              <td className={td}><p className="font-medium">{c.title}</p></td>
              <td className={cn(td, "font-mono text-[11px]")}>
                <span className="font-semibold">{c.method}</span> {c.path}
                {c.body && <p className="text-muted-foreground mt-0.5 break-all">{c.body}</p>}
                {c.capture && Object.keys(c.capture).length > 0 && (
                  <p className="text-muted-foreground mt-0.5">saves {Object.keys(c.capture).map((k) => `{{${k}}}`).join(", ")}</p>
                )}
              </td>
              <td className={cn(td, "tabular-nums")}>{c.expected_status}</td>
              <td className={cn(td, "text-muted-foreground")}>{(c.expected_body_contains ?? []).join(" · ") || "—"}</td>
              <td className={td}><Pill tone={PRIORITY_TONE[c.priority] ?? "neutral"}>{c.priority}</Pill></td>
            </tr>
          ))}
        </ReportTable>
      )}
    </div>
  );
}
