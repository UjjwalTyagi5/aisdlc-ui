"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck, Download, Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { MarkdownReport } from "@/components/app/markdown-report";
import { Callout, Pill, ReportTable, td, th, type PillTone } from "@/components/app/report-primitives";
import type { UseRaiseForApprovalResult } from "@/hooks/use-raise-for-approval";
import { getArtifactPage } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import { approvalState } from "@/lib/documents/report-document";
import type { Artifact } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/**
 * The Code Review page's Checklist tab.
 *
 * A checklist is a document like any other — filed as Word, raised for approval — but it
 * is laid out here as checks, not prose. The agent's `create_review_checklist` writes the
 * page copy in ONE fixed shape (code_review_agent/checklist.py): an `## Section` heading
 * and a `| # | Check | How to verify | Status | Note |` table per section. `parseChecklist`
 * reads that shape back; the two change together. A checklist written any other way (by
 * hand, or before the tool existed) is shown as the document it is.
 */

export interface ChecklistItem { n: string; check: string; how: string; status: string; note: string }
export interface ParsedChecklist {
  title: string | null;
  intro: string;
  sections: { section: string; items: ChecklistItem[] }[];
}

const HEADER = /^\|\s*#\s*\|\s*Check\s*\|\s*How to verify\s*\|\s*Status\s*\|\s*Note\s*\|\s*$/i;
const SEPARATOR = /^\|?\s*:?-{2,}/;

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function parseChecklist(markdown: string): ParsedChecklist {
  const out: ParsedChecklist = { title: null, intro: "", sections: [] };
  const intro: string[] = [];
  let current: ParsedChecklist["sections"][number] | null = null;
  let inTable = false;
  for (const raw of (markdown || "").replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const h1 = /^#\s+(.+)$/.exec(line);
    if (h1 && !out.title && !current) { out.title = h1[1]!.trim(); continue; }
    const h2 = /^##\s+(.+)$/.exec(line);
    if (h2) { current = { section: h2[1]!.trim(), items: [] }; out.sections.push(current); inTable = false; continue; }
    if (current && HEADER.test(line)) { inTable = true; continue; }
    if (inTable && SEPARATOR.test(line)) continue;
    if (inTable && current && line.startsWith("|")) {
      const [n = "", check = "", how = "", status = "", note = ""] = cells(line);
      current.items.push({ n, check, how: how === "—" ? "" : how, status, note: note === "—" ? "" : note });
      continue;
    }
    inTable = false;
    if (!current && line.trim()) intro.push(line.trim());
  }
  out.sections = out.sections.filter((s) => s.items.length > 0);
  out.intro = intro.join("\n");
  return out;
}

const STATUS_TONE: Record<string, PillTone> = { Pass: "success", Fail: "danger", "N/A": "neutral", "To check": "warning" };

/** This project's checklists, newest first — Code Review documents named as one. */
export function checklistDocuments(documents: readonly Artifact[] | null | undefined): Artifact[] {
  return (documents ?? [])
    .filter((d) => d.stage === "code_review" && d.type !== "story" && /checklist/i.test(d.title))
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

export function ReviewChecklistView({ documents, selectedId, onSelect, approvals, onAsk, busy, project }: {
  documents: readonly Artifact[] | null | undefined;
  selectedId: string | null;
  onSelect: (id: string) => void;
  approvals: UseRaiseForApprovalResult;
  onAsk: () => void;
  busy: boolean;
  project?: string;
}) {
  const checklists = checklistDocuments(documents);
  const doc = checklists.find((d) => d.id === selectedId) ?? checklists[0] ?? null;
  const pageQ = useQuery({
    queryKey: qk.artifacts.page(doc?.id ?? ""),
    queryFn: () => getArtifactPage(doc!.id as never),
    enabled: !!doc,
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (documents == null) return <div className="p-6"><LoadingState variant="card" /></div>;
  if (!doc) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState
          icon={ClipboardCheck}
          title="No checklist yet"
          description="Ask the agent for a code review checklist. It fills this tab and files the Word document in Documents. With a review open, each check is marked from that review's results."
          action={
            <Button onClick={onAsk} disabled={busy}>
              <Sparkles className="size-4" aria-hidden />Create a checklist
            </Button>
          }
        />
      </div>
    );
  }

  const parsed = pageQ.data ? parseChecklist(pageQ.data.markdown) : null;
  const items = parsed?.sections.flatMap((s) => s.items) ?? [];
  const count = (status: string) => items.filter((i) => i.status === status).length;
  const state = approvalState(doc);

  return (
    <article className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-brand-bright text-[11px] font-semibold uppercase tracking-wider">Code review checklist</p>
          <h2 className="text-lg font-semibold">{parsed?.title ?? doc.title}</h2>
          <p className="text-muted-foreground text-xs">
            {doc.title} · {new Date(doc.createdAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {checklists.length > 1 && (
            <select
              aria-label="Checklist"
              className="bg-background h-8 rounded-md border px-2 text-xs"
              value={doc.id}
              onChange={(e) => onSelect(e.target.value)}
            >
              {checklists.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title} · {new Date(c.createdAt).toLocaleDateString()}
                </option>
              ))}
            </select>
          )}
          <Pill tone={state.tone} className="px-2.5 py-1 text-xs">{state.label}</Pill>
          {doc.status === "draft" && approvals.mayRaise(doc.stage) && (
            <Button size="sm" className="h-8 gap-1.5 text-xs" disabled={approvals.raisingId === doc.id} onClick={() => approvals.raise(doc)}>
              {approvals.raisingId === doc.id && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
              Raise for approval
            </Button>
          )}
          {doc.downloadUrl && (
            <Button asChild size="sm" variant="outline" className="h-8 gap-1.5 text-xs">
              <a href={doc.downloadUrl} download>
                <Download className="size-3.5" aria-hidden />Download Word
              </a>
            </Button>
          )}
        </div>
      </header>

      {pageQ.isLoading ? (
        <LoadingState variant="card" />
      ) : pageQ.isError || !pageQ.data ? (
        <Callout tone="warning" title="No page view">
          This checklist has no page view (it was uploaded, or created before checklists were kept as pages). The Word file is unaffected.
        </Callout>
      ) : parsed && parsed.sections.length > 0 ? (
        <>
          {parsed.intro && <p className="text-muted-foreground text-sm">{parsed.intro.split("\n")[0]}</p>}
          <div className="flex flex-wrap gap-2 text-xs">
            <Pill tone="neutral">{items.length} checks</Pill>
            <Pill tone="success">{count("Pass")} pass</Pill>
            <Pill tone="danger">{count("Fail")} fail</Pill>
            <Pill tone="warning">{count("To check")} to check</Pill>
            <Pill tone="neutral">{count("N/A")} not applicable</Pill>
          </div>
          {parsed.sections.map((sec) => (
            <section key={sec.section} className="space-y-2">
              <h3 className="text-sm font-semibold">{sec.section}</h3>
              <ReportTable dense head={<><th className={cn(th, "w-10")}>#</th><th className={th}>Check</th><th className={th}>How to verify</th><th className={cn(th, "w-28")}>Status</th><th className={th}>Note</th></>}>
                {sec.items.map((it) => (
                  <tr key={`${sec.section}-${it.n}`}>
                    <td className={cn(td, "text-muted-foreground font-mono text-[11px]")}>{it.n}</td>
                    <td className={cn(td, "font-medium")}>{it.check}</td>
                    <td className={cn(td, "text-muted-foreground")}>{it.how || "—"}</td>
                    <td className={td}><Pill tone={STATUS_TONE[it.status] ?? "neutral"}>{it.status || "To check"}</Pill></td>
                    <td className={cn(td, "text-muted-foreground")}>{it.note || "—"}</td>
                  </tr>
                ))}
              </ReportTable>
            </section>
          ))}
        </>
      ) : (
        <MarkdownReport
          markdown={pageQ.data.markdown}
          filename={pageQ.data.filename || doc.title}
          project={project}
          status={state.label}
          generatedAt={doc.createdAt}
        />
      )}
    </article>
  );
}
