"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  BookText, Boxes, Download, FileText, GitBranch, GitPullRequest, History,
  ListChecks, MessageSquare, Notebook, ScrollText, Sparkles, BookOpen,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { MarkdownMessage } from "@/components/app/markdown-message";
import { DocTargetDialog } from "@/components/app/doc-target-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useSession } from "@/hooks/use-session";
import { getProject } from "@/lib/api/projects";
import { getDocSet } from "@/lib/api/documentation";
import { qk } from "@/lib/api/query-keys";
import type { PrepareDocResult, GeneratedDoc } from "@/lib/schemas/documentation";
import type { ProjectId } from "@/lib/schemas";

const TYPE_LABEL: Record<string, string> = {
  doc_set: "Doc set", overview: "Overview", sdd: "Design", api_reference: "API",
  code_summary: "Code", changelog: "Changelog", release_notes: "Release notes",
  rtm: "RTM", run_summary: "Run summary", compliance: "Compliance",
  runbook_update: "Runbook update", knowledge_article: "Knowledge article", custom: "Doc",
};

interface QuickAction { key: string; label: string; icon: React.ComponentType<{ className?: string }>; prompt: string; }
const QUICK_ACTIONS: QuickAction[] = [
  { key: "doc_set", label: "Doc set", icon: BookText, prompt: "Generate the full documentation set (Overview, Software Design Document, API Reference, and Code & Change Summary), saving each as its own document." },
  { key: "changelog", label: "Changelog", icon: History, prompt: "Generate a grouped changelog from the git history and save it." },
  { key: "release_notes", label: "Release notes", icon: Notebook, prompt: "Generate business-readable release notes (features, fixes, breaking changes, migration steps) and save them." },
  { key: "rtm", label: "Traceability matrix", icon: ListChecks, prompt: "Generate a Requirements Traceability Matrix (requirement → design → code → test → finding) from the upstream artifacts and repo, and save it." },
  { key: "run_summary", label: "Run summary", icon: FileText, prompt: "Generate an executive run summary (scope delivered, quality posture, outstanding risks) and save it." },
  { key: "compliance", label: "Compliance pack", icon: Boxes, prompt: "Generate a SOC 2 / ISO 27001 compliance evidence pack from the available gate decisions, sign-offs, SBOM, and audit trail, and save it." },
  { key: "runbook_update", label: "Runbook update", icon: ScrollText, prompt: "Read the existing runbook for this system from the connected Azure DevOps Wiki or SharePoint, diff it against what changed on this branch/PR, and save a runbook update covering the sections that need to change and their updated content." },
  { key: "knowledge_article", label: "Knowledge article", icon: BookOpen, prompt: "Check whether a knowledge article already exists for the issue fixed on this branch/PR. If one exists, propose an update to it; if not, generate a new knowledge article from the standard template. Save it." },
];

export default function DocumentationPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  useSession({ required: true });

  const projectQ = useQuery({ queryKey: qk.projects.detail(id), queryFn: () => getProject(id) });

  const [prepared, setPrepared] = React.useState<PrepareDocResult | null>(null);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [selId, setSelId] = React.useState<string | null>(null);

  const chat = useAgentChat({ agent: "documentation", context: { page: "Documentation", project_id: id } });

  const docsetQ = useQuery({
    queryKey: qk.documentation.docset(id, chat.sessionId ?? ""),
    queryFn: () => getDocSet(id, chat.sessionId ?? ""),
    enabled: !!prepared && !!chat.sessionId,
    refetchInterval: chat.busy ? 3500 : false,
  });
  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy) docsetQ.refetch();
    prevBusy.current = chat.busy;
  }, [chat.busy, docsetQ]);

  // Memoised so the `?? []` fallback keeps a stable identity between renders —
  // the auto-select effect below depends on it.
  const docs: GeneratedDoc[] = React.useMemo(
    () => docsetQ.data?.documents ?? [],
    [docsetQ.data],
  );
  const prUrl = docsetQ.data?.pr_url ?? null;
  // Auto-select the newest doc when the list grows.
  React.useEffect(() => {
    const last = docs[docs.length - 1];
    if (!last) { setSelId(null); return; }
    if (!selId || !docs.some((d) => d.id === selId)) setSelId(last.id);
  }, [docs, selId]);
  const selected = docs.find((d) => d.id === selId) ?? null;

  const onPrepared = (r: PrepareDocResult) => { setPrepared(r); };
  const runAction = (prompt: string) => { setChatOpen(false); void chat.send(prompt); };
  const openPr = () => { setChatOpen(false); void chat.send("Open a documentation PR with all the generated documents."); };

  const download = (doc: GeneratedDoc) => {
    const blob = new Blob([doc.contents], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = doc.filename || "document.md";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  if (projectQ.isLoading) return <div className="w-full p-4 md:px-10 md:py-8"><LoadingState variant="card" /></div>;
  if (projectQ.isError || !projectQ.data)
    return <div className="w-full p-4 md:px-10 md:py-8"><ErrorState title="Project not found"
      description={projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."} onRetry={() => projectQ.refetch()} /></div>;

  const targetChip = prepared
    ? `${prepared.repo_name} @ ${prepared.branch}`
    : docsetQ.data?.context.repo_name ? `${docsetQ.data.context.repo_name} @ ${docsetQ.data.context.source_branch}` : null;
  const langs = prepared?.languages ?? docsetQ.data?.context.languages ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">Documentation</h1>
            <p className="text-muted-foreground text-xs">
              {targetChip ? (
                <span className="inline-flex flex-wrap items-center gap-1">
                  <GitBranch className="size-3" aria-hidden />
                  <span className="font-mono">{targetChip}</span>
                  {langs.length > 0 && <><span className="opacity-50">·</span><span>{langs.slice(0, 3).join(", ")}</span></>}
                </span>
              ) : <span>Generate enterprise documentation from a branch or PR — ask for exactly what you need.</span>}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <BookText className="size-4" aria-hidden />{prepared ? "New documentation" : "Open docs workspace"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setChatOpen(true)} disabled={!prepared}>
              <MessageSquare className="size-4" aria-hidden />Chat
            </Button>
          </div>
        </div>
      </div>

      {!prepared ? (
        <div className="flex-1 overflow-auto">
          <div className="mx-auto max-w-xl px-4 py-12">
            <EmptyState icon={BookText} title="No documentation workspace yet"
              description="Pick a branch or PR. The agent clones it read-only, folds in any existing platform artifacts, and generates the deliverables you ask for — each saved to a file and shown in the list."
              action={<Button onClick={() => setPickerOpen(true)}><BookText className="size-4" aria-hidden />Open docs workspace</Button>} />
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          {/* Quick-action bar */}
          <div className="from-brand-bright/[0.06] flex flex-wrap items-center gap-1.5 border-b bg-gradient-to-r to-transparent px-3 py-2">
            <span className="text-brand-bright mr-1 inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider">
              <Sparkles className="size-3" aria-hidden />Generate
            </span>
            {QUICK_ACTIONS.map((a) => (
              <Button key={a.key} variant="outline" size="sm"
                className="border-brand-bright/30 bg-brand-bright/[0.04] hover:bg-brand-bright/15 hover:border-brand-bright/55 hover:text-brand-bright h-7 gap-1.5 text-xs transition-colors"
                onClick={() => runAction(a.prompt)} disabled={chat.busy}>
                <a.icon className="text-brand-bright size-3.5" aria-hidden />{a.label}
              </Button>
            ))}
            {prUrl ? (
              <a className="ml-auto" href={prUrl} target="_blank" rel="noreferrer">
                <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs"><GitPullRequest className="size-3.5" aria-hidden />View docs PR</Button>
              </a>
            ) : docs.length > 0 ? (
              <RequireRole capability="run:trigger" fallback={null}>
                <Button size="sm"
                  className="from-brand-gradient-from to-brand-gradient-to ml-auto h-7 gap-1.5 bg-gradient-to-br text-xs font-semibold text-white"
                  onClick={openPr} disabled={chat.busy}>
                  <GitPullRequest className="size-3.5" aria-hidden />Open docs PR
                </Button>
              </RequireRole>
            ) : null}
          </div>

          {/* Split: left doc list + viewer */}
          <div className="grid min-h-0 flex-1 grid-cols-[260px_1fr] overflow-hidden">
            <aside className="min-h-0 overflow-auto border-r">
              <div className="text-muted-foreground border-b px-3 py-2 text-[11px] font-medium uppercase tracking-wider">
                Documents{docs.length > 0 && <span className="ml-1 lowercase opacity-70">({docs.length})</span>}
              </div>
              {docs.length === 0 ? (
                <p className="text-muted-foreground p-3 text-xs">
                  {chat.busy ? "Generating…" : "Nothing yet — use a Generate button above or open Chat and ask."}
                </p>
              ) : (
                <ul className="p-2">
                  {docs.map((d) => (
                    <li key={d.id}>
                      <button type="button" onClick={() => setSelId(d.id)}
                        className={cn("flex w-full items-start gap-2 rounded-md border-l-2 px-2 py-1.5 text-left transition-colors",
                          d.id === selId
                            ? "border-brand-bright bg-brand-bright/10 text-foreground"
                            : "hover:bg-accent/50 border-transparent")}>
                        <FileText className={cn("mt-0.5 size-3.5 shrink-0", d.id === selId ? "text-brand-bright" : "opacity-60")} aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-medium">{d.title || d.filename}</span>
                          <span className="text-muted-foreground block truncate font-mono text-[10px]">{d.filename}</span>
                        </span>
                        <Badge variant="outline" className={cn("shrink-0 text-[9px]", d.id === selId && "border-brand-bright/40 text-brand-bright")}>{TYPE_LABEL[d.type] ?? "Doc"}</Badge>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </aside>

            <div className="flex min-h-0 flex-col overflow-hidden">
              {selected ? (
                <>
                  <div className="flex items-center justify-between gap-2 border-b px-4 py-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{selected.title || selected.filename}</p>
                      <p className="text-muted-foreground truncate font-mono text-[10px]">{selected.filename} · {(selected.bytes / 1024).toFixed(1)} KB</p>
                    </div>
                    <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs" onClick={() => download(selected)}>
                      <Download className="size-3.5" aria-hidden />Download
                    </Button>
                  </div>
                  <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
                    <div className="mx-auto max-w-3xl">
                      <MarkdownMessage content={selected.contents} />
                    </div>
                  </div>
                </>
              ) : chat.busy ? (
                <div className="mx-auto max-w-xl px-4 py-12">
                  <EmptyState icon={Sparkles} title="Generating…"
                    description="The agent is reading the repo and any upstream artifacts, then writing your document. It will appear in the list on the left." variant="plain" />
                </div>
              ) : (
                <div className="mx-auto max-w-xl px-4 py-12">
                  <EmptyState icon={ScrollText} title="Pick what to generate"
                    description="Use a Generate button above for a specific deliverable, or open Chat and ask for anything — e.g. “write the API reference” or “document the auth module”." variant="plain" />
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <DocTargetDialog open={pickerOpen} onOpenChange={setPickerOpen} projectId={id} onPrepared={onPrepared} />

      <AgentChatDrawer
        open={chatOpen} onOpenChange={setChatOpen}
        context={{ page: "Documentation", artifactTitle: targetChip ?? undefined }}
        messages={chat.messages} onSend={chat.send} busy={chat.busy}
        disabledReason={prepared ? undefined : "Open a docs workspace first."}
        starterSuggestions={["Generate the full documentation set.", "Write the API reference.", "Generate release notes for this branch."]}
      />
    </div>
  );
}
