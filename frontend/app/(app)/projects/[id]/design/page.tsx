"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

import { AdrViewer } from "@/components/app/adr-viewer";
import { DocumentCard } from "@/components/app/document-card";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ModelSelector } from "@/components/app/model-selector";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useChatDeepLink } from "@/hooks/use-chat-deep-link";
import { DocumentList } from "@/components/app/document-list";
import { GeneratedDocuments } from "@/components/app/generated-documents";
import { StageVersionPanel } from "@/components/app/stage-version-panel";
import { ActivityTimeline } from "@/components/app/activity-timeline";
import { MermaidRenderer } from "@/components/app/mermaid-renderer";
import { MonacoViewer } from "@/components/app/monaco-viewer";
import { OpenApiViewer } from "@/components/app/openapi-viewer";
import { RequireRole } from "@/components/auth/require-role";

import { useSession } from "@/hooks/use-session";
import { useArtifactApproval } from "@/hooks/use-artifact-approval";
import { listArtifacts, updateArtifact } from "@/lib/api/artifacts";
import { getProject } from "@/lib/api/projects";
import { getRunSteps, listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type {
  Artifact,
  ArtifactId,
  ArtifactType,
  ProjectId,
} from "@/lib/schemas";

const DESIGN_TYPES: ArtifactType[] = [
  "hld",
  "lld",
  "c4_diagram",
  "openapi_spec",
  "db_schema",
  "adr",
  // Generated-file artifacts from the Design chat (docx/ppt/diagram) — chat_artifacts.
  "document",
  "presentation",
  "diagram",
];

export default function DesignPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id as ProjectId;
  const searchParams = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { role } = useSession({ required: true });


  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });
  const artifactsQ = useQuery({
    queryKey: qk.artifacts.forProject(projectId, "design"),
    queryFn: () => listArtifacts(projectId, { phase: "design" }),
  });
  // The upstream Requirements-phase stories — the Design agent designs FROM these.
  // Distinct query key from the design artifacts above so the two caches don't clash.
  const runsQ = useQuery({
    queryKey: qk.runs.forProject(projectId),
    queryFn: () => listRuns({ projectId, pageSize: 20 }),
  });

  const designs = React.useMemo(
    () => (artifactsQ.data ?? []).filter((a) => DESIGN_TYPES.includes(a.type)),
    [artifactsQ.data],
  );

  const selectedFromUrl = searchParams.get("artifact");
  const selected = React.useMemo(
    // Explicit selection only — no auto-select. The detail panel stays blank
    // ("Select an artifact") until the user clicks one (which sets ?artifact=).
    () => (selectedFromUrl ? designs.find((a) => a.id === selectedFromUrl) ?? null : null),
    [designs, selectedFromUrl],
  );

  const selectArtifact = React.useCallback(
    (a: Artifact) => {
      const next = new URLSearchParams(searchParams);
      next.set("artifact", a.id);
      router.replace(`/projects/${projectId}/design?${next.toString()}`);
    },
    [router, projectId, searchParams],
  );

  const approval = useArtifactApproval(projectId);

  const onArtifactDeleted = React.useCallback(
    (a: { id: string }) => {
      // `selected` resolves through designs.find(), so it goes null on its own once the
      // list refetches — but ?artifact= would linger in the URL, and a copied link would
      // point at an artifact that no longer exists.
      if (selectedFromUrl === a.id) {
        const next = new URLSearchParams(searchParams);
        next.delete("artifact");
        const qs = next.toString();
        router.replace(`/projects/${projectId}/design${qs ? `?${qs}` : ""}`);
      }
    },
    [selectedFromUrl, searchParams, router, projectId],
  );

  // Chat drawer — talk directly to the Design agent. It consumes the imported user
  // stories through `context.requirements` (the backend formats pipeline_context
  // .requirements into the agent input).
  const [chatOpen, setChatOpen] = React.useState(false);
  // A `?session=` link from the project overview opens the drawer on that
  // conversation rather than a blank one.
  const linkedSession = useChatDeepLink(setChatOpen);
  const [agentModel, setAgentModel] = React.useState<string>();
  const chat = useAgentChat({
    openSessionId: linkedSession,
    agent: "design",
    projectId,
    // The page's model picker. Without it the chat resolved with no model and ran on
    // whichever provider connection sorts first by display name.
    offeringId: agentModel,
    sessionKey: projectId,
    onArtifact: () => {
      queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      queryClient.invalidateQueries({ queryKey: qk.runs.forProject(projectId) });
    },
    context: {
      // Lets the backend resolve this project's per-stage MCP selection for design.
      project_id: projectId,
      page: "Design",
      artifactTitle: selected?.title,
      // NO REQUIREMENTS ARE INJECTED. The agent calls read_project_requirements when
      // the conversation warrants it. Pushing every story in before the user had typed
      // fed it whatever the board held — Epics and setup Tasks included — and it
      // designed a system for them.
    },
  });

  // Approval mutation (shared pattern)
  const decisionMutation = useMutation({
    mutationFn: async (input: {
      id: ArtifactId;
      status: "approved" | "rejected";
      reason?: string;
    }) => updateArtifact(input.id, { status: input.status }),
    onSuccess: (_data, vars) => {
      toast.success(vars.status === "approved" ? "Approved" : "Rejected", {
        description: vars.reason ? `“${vars.reason}”` : undefined,
      });
      queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
    },
    onError: (err) =>
      toast.error("Couldn't submit decision", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  // Shortcuts: j/k nav, a approve, r reject, c chat
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      if (el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (designs.length === 0) return;

      const idx = selected ? designs.findIndex((a) => a.id === selected.id) : 0;
      if (e.key === "j") {
        e.preventDefault();
        const next = designs[Math.min(designs.length - 1, idx + 1)];
        if (next) selectArtifact(next);
      } else if (e.key === "k") {
        e.preventDefault();
        const prev = designs[Math.max(0, idx - 1)];
        if (prev) selectArtifact(prev);
      // NO `a`/`r` SHORTCUTS ANY MORE. They drove the approval card that used to be on
      // this pane: `r` clicked its reject button by test id, which no longer exists, and
      // `a` would have become an approve with no visible control beside it — a
      // keystroke that silently accepts a document into the project's record is the
      // last thing this screen should offer.
      } else if (e.key === "c") {
        e.preventDefault();
        setChatOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [designs, selected, selectArtifact, decisionMutation]);

  const selectedRun = React.useMemo(() => {
    if (!selected) return null;
    return runsQ.data?.items.find((r) => r.id === selected.runId) ?? null;
  }, [runsQ.data, selected]);

  const stepsQ = useQuery({
    queryKey: selectedRun ? qk.runs.steps(selectedRun.id) : ["no-run"],
    queryFn: () => getRunSteps(selectedRun!.id),
    enabled: !!selectedRun,
  });

  // --- render ---
  if (projectQ.isLoading) {
    return (
      <div className="w-full space-y-4 p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }
  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState
          title="Project not found"
          description={
            projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."
          }
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Design</h1>
            <p className="text-muted-foreground text-xs">
              {designs.length} {designs.length === 1 ? "artifact" : "artifacts"} · signed in as{" "}
              <span className="text-foreground font-mono">{role}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label="Design agent model"
              projectId={projectId}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <RequireRole capability="run:trigger">
              <Button size="sm" onClick={() => setChatOpen(true)}>
                <MessageSquare className="size-4" aria-hidden />
                Run Design agent
                <kbd className="bg-muted ml-1 rounded border px-1 font-mono text-[10px]">C</kbd>
              </Button>
            </RequireRole>
          </div>
        </div>
      </div>

      <div className="grid flex-1 gap-0 overflow-hidden md:grid-cols-[340px_1fr] xl:grid-cols-[360px_1fr]">
        <aside
          aria-label="Design artifacts"
          className="flex min-h-0 flex-col overflow-auto border-b p-3 md:border-b-0 md:border-r"
        >
          <StageVersionPanel
            projectId={projectId}
            phase="design"
            className="mb-3 shrink-0"
          />
          {/* ONE LIST, NOT TWO. This sidebar used to stack a DocumentList above an
              ArtifactList whose DESIGN_TYPES included "document", "presentation" and
              "diagram" — exactly the types `_artifact_type_for` assigns to a generated
              file. Both matched the same rows, so every document the Design agent
              produced appeared twice: once with its approval state and once as a
              selectable row with a delete button. Two empty states stacked on a new
              project made the duplication obvious even with nothing in it.

              The merged list does both jobs. It selects (driving the detail pane on the
              right) and it carries the approval state and actions, which the artifact
              list never had — so a design artifact can now be raised and approved like
              anything else in the project's record. */}
          <DocumentList
            projectId={projectId}
            items={artifactsQ.data ?? null}
            stage="design"
            title="Design artifacts"
            description="Diagrams, specifications and generated files. Approved ones are part of the project's record."
            selectedId={selected?.id}
            onSelect={selectArtifact}
            onDeleted={onArtifactDeleted}
            className="min-h-0 flex-1"
          />
        </aside>

        <main className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex-1 overflow-auto">
            {/* Generated documents — anything the chat produces surfaces here on the
                MAIN screen, per the self-contained agent-page design language. */}
            {chat.documents.length > 0 && (
              <div className="mx-auto max-w-5xl px-4 pt-4 md:px-6">
                <GeneratedDocuments
                  documents={chat.documents}
                  projectId={projectId}
                  stage="design"
                  artifacts={artifactsQ.data ?? null}
                  className="bg-muted/20 rounded-lg border p-3"
                />
              </div>
            )}
            {selected ? (
              <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
                <header className="space-y-1">
                  <h2 className="text-lg font-semibold">{selected.title}</h2>
                  <p className="text-muted-foreground text-xs">
                    {selected.type.replace(/_/g, " ")} · v{selected.version} · updated{" "}
                    {new Date(selected.updatedAt).toLocaleString(undefined, {
                      dateStyle: "medium",
                      timeStyle: "short",
                    })}
                  </p>
                </header>

                <ArtifactViewer artifact={selected} approval={approval} />

                {/* THE APPROVAL CARD USED TO SIT HERE, and it was three wrong things
                    at once.

                    It was a THIRD place to decide the same document — the list row on
                    the left already offers Approve/Reject, and Requests & Approvals is
                    the queue built for exactly this.

                    It DESCRIBED THE WRONG CONSEQUENCE: "Approval accepts the design and
                    unlocks Development" is the STAGE gate's copy (lib/agents.ts). What
                    the buttons actually did was accept one document into the project's
                    record, which unlocks nothing. An approval control that misstates
                    what approving does is worse than no control.

                    And it INVENTED THE DECIDER: `decidedBy={me}` is whoever is looking
                    at the screen, not whoever decided. It only ever read correctly for
                    the person who had just clicked it.

                    The status is still visible — `ArtifactViewer` shows it, and the row
                    on the left carries the chip and the actions. Nothing was lost by
                    removing this except the ability to be told the wrong thing. */}
              </div>
            ) : (
              <EmptyState
                title="Select an artifact"
                description="Pick a design artifact from the list."
                variant="plain"
                className="mt-10"
              />
            )}
          </div>

          {selected && (
            <div className="bg-muted/30 max-h-48 overflow-auto border-t px-4 py-3 md:px-6">
              <h3 className="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wider">
                Activity
              </h3>
              <ActivityTimeline steps={stepsQ.data ?? []} className="max-w-3xl" />
              {stepsQ.data?.length === 0 && (
                <p className="text-muted-foreground text-xs">No steps recorded yet.</p>
              )}
            </div>
          )}
        </main>
      </div>

      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: "Design", artifactTitle: selected?.title }}
        messages={chat.messages}
        onSend={chat.send}
        busy={chat.busy}
        onStop={chat.cancel}
        sessions={chat.sessions}
        activeSessionId={chat.sessionId}
        onSelectSession={chat.selectSession}
        onNewChat={chat.newChat}
        attachments={chat.attachments}
        onAttachFiles={chat.attachFiles}
        onRemoveAttachment={chat.removeAttachment}
      />
    </div>
  );
}

// ───────── Viewer switcher ─────────

function ArtifactViewer({
  artifact,
  approval,
}: {
  artifact: Artifact;
  approval: ReturnType<typeof useArtifactApproval>;
}) {
  const { body } = artifact;
  switch (body.kind) {
    case "c4_diagram":
      return <MermaidRenderer source={body.source} height={440} />;
    case "openapi_spec":
      return <OpenApiViewer source={body.yaml} className="h-[540px]" />;
    case "db_schema":
      return (
        <MonacoViewer
          value={body.sql}
          language="sql"
          filename={`${artifact.title}.sql`}
          height={440}
        />
      );
    case "adr":
      return <AdrViewer markdown={body.markdown} />;
    case "document":
      return (
        <DocumentCard
          artifactId={artifact.id}
          filename={body.filename}
          contentType={body.contentType}
          sizeBytes={body.sizeBytes}
          stored={body.stored}
          awaitingApproval={body.awaitingApproval}
          rejected={body.rejected}
          // Handlers only for someone who may decide — the card renders no controls
          // without them, so an unprivileged viewer never sees a button that 403s.
          onApprove={approval.canDecide ? () => approval.approve(artifact.id) : undefined}
          onReject={approval.canDecide ? () => approval.reject(artifact.id) : undefined}
          deciding={approval.decidingId === artifact.id}
        />
      );
    case "raw":
      // AdrViewer captions its output "Architecture Decision Record", which is right
      // for an ADR and wrong for everything else that lands here.
      return <AdrViewer markdown={body.markdown} />;
    default:
      return (
        <EmptyState
          title="Nothing to render"
          description={`No dedicated viewer for "${artifact.type}" yet.`}
          variant="card"
        />
      );
  }
}

