"use client";

/**
 * StageWorkbench — the shared Requirements-style shell for a single SDLC stage.
 *
 * Gives any stage page the same anatomy as the Requirements/Design pages without
 * duplicating ~400 lines per stage: a header (ModelSelector + Run/Ask agent), a
 * left artifact list (filtered to this stage's phase), a generic detail pane with
 * an approval gate, an activity dock, and a streaming agent chat (useAgentChat)
 * scoped to this stage's agent — carrying project_id so the backend binds this
 * project's per-stage MCP tools.
 *
 * Stage-specific pages (Requirements, Design, Development, Testing, Deployment)
 * keep their bespoke editors/viewers; this is for stages that only need the
 * standard list/detail/chat surface (Code Review, Security, Documentation).
 */
import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

import { ActivityTimeline } from "@/components/app/activity-timeline";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ApprovalCard } from "@/components/app/approval-card";
import { ArtifactList } from "@/components/app/artifact-list";
import { DocumentCard } from "@/components/app/document-card";
import { ModelSelector } from "@/components/app/model-selector";
import { useAgentChat } from "@/hooks/use-agent-chat";

import { useSession } from "@/hooks/use-session";
import { useDeleteArtifact } from "@/hooks/use-delete-artifact";
import { GATE_POLICY } from "@/lib/agents";
import { listArtifacts, updateArtifact } from "@/lib/api/artifacts";
import { getProject } from "@/lib/api/projects";
import { getRunSteps, listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type {
  Artifact,
  ArtifactId,
  Phase,
  ProjectId,
  Step,
  UserRef,
} from "@/lib/schemas";

export interface StageWorkbenchProps {
  /** The pipeline phase whose artifacts this page shows + filters on. */
  phase: Phase;
  /**
   * The chat agent id routed by the BFF (/api/chat → agentWsPath). When the agent
   * has no dedicated WS yet (review/security/documentation) the BFF falls back to
   * the orchestrator, so chat still works.
   */
  agent: string;
  /** Page + chat heading, e.g. "Code Review". */
  title: string;
  /** Label for the primary action button, e.g. "Run Code Review agent". */
  runLabel: string;
  emptyTitle?: string;
  emptyDescription?: string;
}

export function StageWorkbench({
  phase,
  agent,
  title,
  runLabel,
  emptyTitle = "No artifacts yet",
  emptyDescription = "Run the agent to generate this stage's artifacts.",
}: StageWorkbenchProps) {
  // Gate semantics (type + owner + copy) derived from the blueprint §4.3 / §7.1.
  const gate = GATE_POLICY[phase];
  const params = useParams<{ id: string }>();
  const projectId = params.id as ProjectId;
  const searchParams = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, role } = useSession({ required: true });
  // Route segment === phase for the stages that use this shell.
  const routeSegment = phase;

  const me: UserRef = {
    id: user.id as UserRef["id"],
    name: user.name,
    email: user.email,
    avatarUrl: user.avatarUrl,
    initials: user.initials,
  };

  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });
  const artifactsQ = useQuery({
    queryKey: qk.artifacts.forProject(projectId),
    queryFn: () => listArtifacts(projectId, { phase }),
  });
  const runsQ = useQuery({
    queryKey: qk.runs.forProject(projectId),
    queryFn: () => listRuns({ projectId, pageSize: 20 }),
  });

  const items = React.useMemo(() => artifactsQ.data ?? [], [artifactsQ.data]);

  const selectedFromUrl = searchParams.get("artifact");
  const selected = React.useMemo(
    () =>
      items.find((a) => a.id === selectedFromUrl) ??
      items.find((a) => a.status === "awaiting_approval") ??
      items[0] ??
      null,
    [items, selectedFromUrl],
  );

  const selectArtifact = React.useCallback(
    (a: Artifact) => {
      const next = new URLSearchParams(searchParams);
      next.set("artifact", a.id);
      router.replace(`/projects/${projectId}/${routeSegment}?${next.toString()}`);
    },
    [router, projectId, routeSegment, searchParams],
  );

  const deletion = useDeleteArtifact(projectId, {
    onDeleted: (a) => {
      // Drop ?artifact= when the deleted one was open, so a copied link does not point
      // at an artifact that no longer exists.
      if (searchParams.get("artifact") === a.id) {
        const next = new URLSearchParams(searchParams);
        next.delete("artifact");
        const qs = next.toString();
        router.replace(`/projects/${projectId}/${routeSegment}${qs ? `?${qs}` : ""}`);
      }
    },
  });

  const [chatOpen, setChatOpen] = React.useState(false);
  const [agentModel, setAgentModel] = React.useState<string>();

  const chat = useAgentChat({
    agent,
    projectId,
    sessionKey: projectId,
    onArtifact: () => {
      queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      queryClient.invalidateQueries({ queryKey: qk.runs.forProject(projectId) });
    },
    context: {
      // Lets the backend resolve this project's per-stage MCP selection for this stage.
      project_id: projectId,
      page: title,
      artifactTitle: selected?.title,
    },
  });

  const decisionMutation = useMutation({
    mutationFn: async (input: {
      id: ArtifactId;
      status: "approved" | "rejected";
      reason?: string;
    }) => updateArtifact(input.id, { status: input.status }),
    onSuccess: (_d, vars) => {
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

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      if (el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (items.length === 0) {
        if (e.key === "c") {
          e.preventDefault();
          setChatOpen((o) => !o);
        }
        return;
      }

      const idx = selected ? items.findIndex((a) => a.id === selected.id) : 0;
      if (e.key === "j") {
        e.preventDefault();
        const next = items[Math.min(items.length - 1, idx + 1)];
        if (next) selectArtifact(next);
      } else if (e.key === "k") {
        e.preventDefault();
        const prev = items[Math.max(0, idx - 1)];
        if (prev) selectArtifact(prev);
      } else if (e.key === "a" && selected?.status === "awaiting_approval") {
        e.preventDefault();
        decisionMutation.mutate({ id: selected.id, status: "approved" });
      } else if (e.key === "r" && selected?.status === "awaiting_approval") {
        e.preventDefault();
        document
          .querySelector<HTMLButtonElement>('[data-testid="approval-reject"]')
          ?.click();
      } else if (e.key === "c") {
        e.preventDefault();
        setChatOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selected, selectArtifact, decisionMutation]);

  const selectedRun = React.useMemo(() => {
    if (!selected) return null;
    return runsQ.data?.items.find((r) => r.id === selected.runId) ?? null;
  }, [runsQ.data, selected]);

  const stepsQ = useQuery({
    queryKey: selectedRun ? qk.runs.steps(selectedRun.id) : ["no-run"],
    queryFn: () => getRunSteps(selectedRun!.id),
    enabled: !!selectedRun,
  });

  if (projectQ.isLoading) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
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
      {/* Header / action bar */}
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
            <p className="text-muted-foreground text-xs">
              {items.length} artifact{items.length === 1 ? "" : "s"} · gate:{" "}
              <span className="text-foreground">{gate.ownerLabel}</span>
              {gate.type === "mandatory" && (
                <span className="text-warning"> (mandatory)</span>
              )}{" "}
              · signed in as{" "}
              <span className="text-foreground font-mono">{role}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label={`${title} agent model`}
              projectId={projectId}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />
              {runLabel}
              <kbd className="bg-muted ml-1 rounded border px-1 font-mono text-[10px]">
                C
              </kbd>
            </Button>
          </div>
        </div>
      </div>

      {/* Split content */}
      <div className="grid flex-1 gap-0 overflow-hidden md:grid-cols-[340px_1fr] xl:grid-cols-[360px_1fr]">
        <aside
          aria-label={`${title} artifacts`}
          className="flex min-h-0 flex-col overflow-auto border-b p-3 md:border-b-0 md:border-r"
        >
          <ArtifactList
            items={artifactsQ.isLoading ? null : items}
            selectedId={selected?.id}
            onSelect={selectArtifact}
            onDelete={deletion.onDelete}
            deletingId={deletion.deletingId}
            isLoading={artifactsQ.isLoading}
            emptyTitle={emptyTitle}
            emptyDescription={emptyDescription}
          />
          {deletion.dialog}
        </aside>

        <main className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex-1 overflow-auto">
            {/* Generated documents produced by chat show on the main screen. */}
            {chat.documents.length > 0 && (
              <div className="mx-auto max-w-3xl px-4 pt-4 md:px-6">
                <section className="bg-muted/20 rounded-lg border p-3">
                  <h3 className="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wider">
                    Generated documents
                  </h3>
                  <ul className="space-y-1">
                    {chat.documents.map((d) => (
                      <li key={d.id}>
                        <a
                          href={d.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-info inline-flex items-center gap-1.5 text-sm hover:underline"
                        >
                          <FileText className="size-3.5" aria-hidden />
                          {d.name ?? "document"}
                        </a>
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
            )}

            {selected ? (
              <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
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

                <ArtifactBody artifact={selected} />

                {gate.type === "auto_approve" ? (
                  // Documentation et al. auto-approve on completion (§7.1) — no
                  // human decision; show the policy note instead of approve/reject.
                  <section className="bg-muted/20 rounded-lg border p-4">
                    <h3 className="text-sm font-semibold">{gate.title}</h3>
                    <p className="text-muted-foreground mt-1 text-xs">{gate.description}</p>
                  </section>
                ) : (
                  <ApprovalCard
                    status={selected.status}
                    title={gate.title}
                    description={gate.description}
                    decidedBy={
                      selected.status === "approved" || selected.status === "rejected"
                        ? me
                        : undefined
                    }
                    decidedAt={
                      selected.status === "approved" || selected.status === "rejected"
                        ? selected.updatedAt
                        : undefined
                    }
                    onApprove={
                      selected.status === "awaiting_approval"
                        ? () => decisionMutation.mutate({ id: selected.id, status: "approved" })
                        : undefined
                    }
                    onReject={
                      selected.status === "awaiting_approval"
                        ? (reason) =>
                            decisionMutation.mutate({
                              id: selected.id,
                              status: "rejected",
                              reason,
                            })
                        : undefined
                    }
                    onAskAgent={() => setChatOpen(true)}
                    pending={decisionMutation.isPending}
                    pendingDecision={
                      decisionMutation.isPending && decisionMutation.variables
                        ? decisionMutation.variables.status === "approved"
                          ? "approve"
                          : "reject"
                        : null
                    }
                  />
                )}
              </div>
            ) : (
              <EmptyState
                title={`Select a ${title.toLowerCase()} artifact`}
                description="Pick an item from the list, or run the agent to generate one."
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
              <ActivityTimeline steps={(stepsQ.data ?? []) as Step[]} className="max-w-3xl" />
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
        context={{ page: title, artifactTitle: selected?.title }}
        messages={chat.messages}
        onSend={chat.send}
        busy={chat.busy}
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

/** Generic artifact body renderer — raw markdown when present, else a readable
 * JSON fallback so any artifact type is at least viewable. */
function ArtifactBody({ artifact }: { artifact: Artifact }) {
  if (artifact.body.kind === "document") {
    return (
      <DocumentCard
        artifactId={artifact.id}
        filename={artifact.body.filename}
        contentType={artifact.body.contentType}
        sizeBytes={artifact.body.sizeBytes}
        stored={artifact.body.stored}
      />
    );
  }
  if (artifact.body.kind === "raw") {
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap text-sm">
        {artifact.body.markdown}
      </div>
    );
  }
  return (
    <pre className="bg-muted/40 overflow-auto rounded-lg border p-3 text-xs">
      {JSON.stringify(artifact.body, null, 2)}
    </pre>
  );
}
