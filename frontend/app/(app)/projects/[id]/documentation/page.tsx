"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookText, Boxes, FileText, GitBranch, GitPullRequest, History,
  ListChecks, Loader2, MessageSquare, Notebook, ScrollText, Sparkles, BookOpen,
  Users, GraduationCap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { DocumentList } from "@/components/app/document-list";
import { DocumentReportView, hasReportView } from "@/components/app/document-report-view";
import type { GeneratedDoc } from "@/components/app/generated-documents";
import { ModelSelector } from "@/components/app/model-selector";
import { StageVersionPanel } from "@/components/app/stage-version-panel";
import { DocTargetDialog } from "@/components/app/doc-target-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useChatDeepLink } from "@/hooks/use-chat-deep-link";
import { useRaiseForApproval } from "@/hooks/use-raise-for-approval";
import { useSession } from "@/hooks/use-session";
import { listArtifacts } from "@/lib/api/artifacts";
import { getProject } from "@/lib/api/projects";
import { getDocSet, getPreparedDocs } from "@/lib/api/documentation";
import { qk } from "@/lib/api/query-keys";
import { approvalState } from "@/lib/documents/report-document";
import type { PrepareDocResult } from "@/lib/schemas/documentation";
import type { Artifact, ProjectId } from "@/lib/schemas";

interface QuickAction { key: string; label: string; icon: React.ComponentType<{ className?: string }>; prompt: string; }
const QUICK_ACTIONS: QuickAction[] = [
  { key: "doc_set", label: "Doc set", icon: BookText, prompt: "Generate the full documentation set (Overview, Software Design Document, API Reference, and Code & Change Summary), saving each as its own document." },
  { key: "changelog", label: "Changelog", icon: History, prompt: "Generate a grouped changelog from the git history, tie each commit to the epic or user story it delivers using the upstream requirements and the approved project documents, leave any commit you cannot tie to one unchanged, and save it." },
  { key: "release_notes", label: "Release notes", icon: Notebook, prompt: "Generate business-readable release notes (features, fixes, breaking changes, migration steps) and save them." },
  { key: "rtm", label: "Traceability matrix", icon: ListChecks, prompt: "Generate a Requirements Traceability Matrix (requirement → design → code → test → finding) from the upstream artifacts and repo, and save it." },
  { key: "run_summary", label: "Run summary", icon: FileText, prompt: "Generate an executive run summary (scope delivered, quality posture, outstanding risks) and save it." },
  { key: "compliance", label: "Compliance pack", icon: Boxes, prompt: "Generate a SOC 2 / ISO 27001 compliance evidence pack from the available gate decisions, sign-offs, SBOM, and audit trail, and save it." },
  { key: "runbook_update", label: "Runbook update", icon: ScrollText, prompt: "Read the existing runbook for this system from the connected Azure DevOps Wiki or SharePoint, diff it against what changed on this branch/PR, and save a runbook update covering the sections that need to change and their updated content." },
  { key: "knowledge_article", label: "Knowledge article", icon: BookOpen, prompt: "Check whether a knowledge article already exists for the issue fixed on this branch/PR. If one exists, propose an update to it; if not, generate a new knowledge article from the standard template. Save it." },
  { key: "handover", label: "Handover pack", icon: Users, prompt: "Produce a handover document for a team taking over ownership of this system. Ground it in the repo, the approved project documents and any connected runbook: scope of the handover and what is excluded, systems and the access each needs, how it is deployed, monitored and rolled back, work in flight, known risks with severity, and key contacts with the escalation path. Where something is genuinely undocumented, say so and name who to ask." },
  { key: "kt", label: "KT document", icon: GraduationCap, prompt: "Produce a knowledge-transfer document for somebody joining this project. Ground it in the repo and the approved project documents: what the system does and who depends on it, an architecture tour referencing real paths, the environments and the exact steps to run it locally, the common tasks they will be asked to do, and where everything lives. Expand every project-specific acronym." },
];

export default function DocumentationPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  useSession({ required: true });
  const queryClient = useQueryClient();

  const projectQ = useQuery({ queryKey: qk.projects.detail(id), queryFn: () => getProject(id) });

  const [prepared, setPrepared] = React.useState<PrepareDocResult | null>(null);
  // HYDRATED FROM THE SERVER, where the prepared workspace lives. In React state alone a
  // refresh threw it away: "No documentation workspace yet", and Chat disabled, for a
  // checkout the backend still held — the Deployment page's old bug, here too.
  const preparedQ = useQuery({
    queryKey: ["documentation", "prepared", id],
    queryFn: () => getPreparedDocs(id),
    staleTime: 30_000,
  });
  React.useEffect(() => {
    const s = preparedQ.data;
    if (!s || s.status !== "ready" || prepared) return;
    setPrepared(s as PrepareDocResult);
  }, [preparedQ.data, prepared]);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  // A `?session=` link from the project overview opens the drawer on that
  // conversation rather than a blank one.
  const linkedSession = useChatDeepLink(setChatOpen);
  // The model this page's documents are written with. Without it the chat ran on
  // whichever provider connection resolved first — one whose key had been revoked.
  const [agentModel, setAgentModel] = React.useState<string>();

  // The project's documents — the SAME query the Documents panel reads, so a document
  // raised or approved anywhere updates the panel and the open document's header alike.
  const documentsQ = useQuery({
    queryKey: qk.artifacts.forProject(id),
    queryFn: () => listArtifacts(id),
  });
  const approvals = useRaiseForApproval(id);

  // projectId turns on the durable session the attachments are stored against —
  // see the Code Review page for why `attachFiles` needs one.
  const chat = useAgentChat({
    openSessionId: linkedSession,
    agent: "documentation",
    projectId: id,
    offeringId: agentModel,
    sessionKey: id,
    context: { page: "Documentation", project_id: id },
    // A turn can file a document or send one for approval — both change the panel.
    onArtifact: () => {
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(id) });
    },
  });

  const docsetQ = useQuery({
    queryKey: qk.documentation.docset(id, chat.sessionId ?? ""),
    queryFn: () => getDocSet(id, chat.sessionId ?? ""),
    enabled: !!prepared && !!chat.sessionId,
    refetchInterval: chat.busy ? 3500 : false,
  });
  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy) {
      void docsetQ.refetch();
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(id) });
    }
    prevBusy.current = chat.busy;
  }, [chat.busy, docsetQ, queryClient, id]);

  // THE DOCUMENT IN THE CENTRE, opened by its artifact row — the page copy the agent
  // filed with the Word file, exactly as the Requirements page opens a BRD. It used to
  // render the chat session's in-memory markdown, which a refresh or a new chat threw
  // away while the document itself sat in the panel with nothing to click.
  const [openDoc, setOpenDoc] = React.useState<GeneratedDoc | null>(null);
  const shownDocIds = React.useRef<Set<string>>(new Set());
  React.useEffect(() => {
    const fresh = chat.documents.filter((d) => hasReportView(d) && !shownDocIds.current.has(d.id));
    if (fresh.length === 0) return;
    for (const d of fresh) shownDocIds.current.add(d.id);
    setOpenDoc(fresh[fresh.length - 1]!);
  }, [chat.documents]);
  const openArtifact = React.useCallback((a: Artifact) => {
    setOpenDoc({ id: a.id, name: a.title, url: a.downloadUrl ?? null, documentId: a.id });
  }, []);
  const openRow = openDoc?.documentId
    ? (documentsQ.data ?? []).find((a) => a.id === openDoc.documentId) ?? null
    : null;

  const prUrl = docsetQ.data?.pr_url ?? null;
  const sessionDocs = docsetQ.data?.documents.length ?? 0;
  const onPrepared = (r: PrepareDocResult) => { setPrepared(r); };
  const runAction = (prompt: string) => { setChatOpen(false); void chat.send(prompt); };
  const openPr = () => { setChatOpen(false); void chat.send("Open a documentation PR with all the generated documents."); };

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
            <ModelSelector
              aria-label="Documentation agent model"
              projectId={id}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <BookText className="size-4" aria-hidden />{prepared ? "New documentation" : "Open docs workspace"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setChatOpen(true)} disabled={!prepared}>
              <MessageSquare className="size-4" aria-hidden />Chat
            </Button>
          </div>
        </div>
      </div>

      {!prepared && openDoc ? (
        // A document opened from the panel below, before any docs workspace exists — the
        // documents are this stage's record whether or not the agent has run here.
        <div className="flex min-h-0 flex-1 flex-col overflow-auto">
          <DocumentReportView
            key={openDoc.id}
            doc={openDoc}
            project={projectQ.data?.name}
            status={openRow ? approvalState(openRow).label : undefined}
            actions={openRow && openRow.status === "draft" && approvals.mayRaise(openRow.stage) ? (
              <Button size="sm" className="h-8 gap-1.5 text-xs"
                disabled={approvals.raisingId === openRow.id}
                onClick={() => approvals.raise(openRow)}>
                {approvals.raisingId === openRow.id && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                Raise for approval
              </Button>
            ) : null}
            onClose={() => setOpenDoc(null)}
          />
        </div>
      ) : !prepared ? (
        <div className="flex-1 overflow-auto">
          <div className="mx-auto max-w-xl px-4 py-12">
            <EmptyState icon={BookText} title="No documentation workspace yet"
              description="Pick a branch or PR. The agent clones it read-only, folds in any existing platform artifacts, and generates the deliverables you ask for — each filed as a Word document in Documents."
              action={<Button onClick={() => setPickerOpen(true)}><BookText className="size-4" aria-hidden />Open docs workspace</Button>} />
          </div>
          {/* The documents exist whether or not a docs workspace has been opened —
              they are uploaded and approved on this stage, not generated by it. The
              panel beside the documents is inside the prepared branch below, so without
              this they were unreachable on exactly the projects that have never run the
              agent. */}
          <div className="mx-auto max-w-xl px-4 pb-12">
            <DocumentList projectId={id} stage="documentation" selectedId={openDoc?.documentId ?? null} onSelect={openArtifact} />
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
            ) : sessionDocs > 0 ? (
              <RequireRole capability="run:trigger" fallback={null}>
                <Button size="sm"
                  className="from-brand-gradient-from to-brand-gradient-to ml-auto h-7 gap-1.5 bg-gradient-to-br text-xs font-semibold text-white"
                  onClick={openPr} disabled={chat.busy}>
                  <GitPullRequest className="size-3.5" aria-hidden />Open docs PR
                </Button>
              </RequireRole>
            ) : null}
          </div>

          {/* Split: the stage's documents + the open document */}
          <div className="grid min-h-0 flex-1 grid-cols-[300px_1fr] overflow-hidden">
            {/* ONE LIST. There used to be two: this panel, and the chat session's own
                in-memory list below it — the same document twice, one of which could be
                raised for approval and one of which vanished on refresh. */}
            <aside aria-label="Documents" className="flex min-h-0 flex-col overflow-hidden border-r p-3">
              <StageVersionPanel projectId={id} phase="documentation" className="mb-3 shrink-0" />
              <DocumentList
                projectId={id}
                items={documentsQ.data ?? null}
                stage="documentation"
                className="flex min-h-0 flex-1 flex-col"
                fillHeight
                selectedId={openDoc?.documentId ?? null}
                onSelect={openArtifact}
              />
            </aside>

            <div className="flex min-h-0 flex-col overflow-auto">
              {openDoc ? (
                <DocumentReportView
                  key={openDoc.id}
                  doc={openDoc}
                  project={projectQ.data?.name}
                  status={openRow ? approvalState(openRow).label : undefined}
                  actions={openRow && openRow.status === "draft" && approvals.mayRaise(openRow.stage) ? (
                    <Button size="sm" className="h-8 gap-1.5 text-xs"
                      disabled={approvals.raisingId === openRow.id}
                      onClick={() => approvals.raise(openRow)}>
                      {approvals.raisingId === openRow.id && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                      Raise for approval
                    </Button>
                  ) : null}
                  onClose={() => setOpenDoc(null)}
                />
              ) : chat.busy ? (
                <div className="mx-auto max-w-xl px-4 py-12">
                  <EmptyState icon={Sparkles} title="Generating…"
                    description="The agent is reading the repo and any upstream artifacts, then writing your document. It opens here when it is filed." variant="plain" />
                </div>
              ) : (
                <div className="mx-auto max-w-xl px-4 py-12">
                  <EmptyState icon={ScrollText} title="Pick what to generate"
                    description="Use a Generate button above for a specific deliverable, open a document on the left, or open Chat and ask for anything — e.g. “write the handover document”." variant="plain" />
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
        messages={chat.messages} onSend={chat.send} busy={chat.busy} onStop={chat.cancel}
        sessions={chat.sessions}
        activeSessionId={chat.sessionId}
        onSelectSession={chat.selectSession}
        onNewChat={chat.newChat}
        attachments={chat.attachments}
        onAttachFiles={chat.attachFiles}
        onRemoveAttachment={chat.removeAttachment}
        disabledReason={prepared ? undefined : "Open a docs workspace first."}
        starterSuggestions={["Write the handover document for this system.", "Generate release notes for this branch.", "Send the handover document for approval."]}
      />
    </div>
  );
}
