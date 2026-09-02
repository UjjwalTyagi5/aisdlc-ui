"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

import { AdrViewer } from "@/components/app/adr-viewer";
import { DocumentCard } from "@/components/app/document-card";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ModelSelector } from "@/components/app/model-selector";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { ApprovalCard } from "@/components/app/approval-card";
import { ArtifactList } from "@/components/app/artifact-list";
import { ActivityTimeline } from "@/components/app/activity-timeline";
import { MermaidRenderer } from "@/components/app/mermaid-renderer";
import { MonacoViewer } from "@/components/app/monaco-viewer";
import { OpenApiViewer } from "@/components/app/openapi-viewer";
import { RequireRole } from "@/components/auth/require-role";

import { useSession } from "@/hooks/use-session";
import { useDeleteArtifact } from "@/hooks/use-delete-artifact";
import { listArtifacts, updateArtifact } from "@/lib/api/artifacts";
import { getProject } from "@/lib/api/projects";
import { getRunSteps, listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type {
  Artifact,
  ArtifactId,
  ArtifactType,
  ProjectId,
  UserRef,
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
  const { user, role } = useSession({ required: true });

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
    queryFn: () => listArtifacts(projectId, { phase: "design" }),
  });
  // The upstream Requirements-phase stories — the Design agent designs FROM these.
  // Distinct query key from the design artifacts above so the two caches don't clash.
  const reqStoriesQ = useQuery({
    queryKey: ["artifacts", "project", projectId, "requirements"],
    queryFn: () => listArtifacts(projectId, { phase: "requirements" }),
  });
  const runsQ = useQuery({
    queryKey: qk.runs.forProject(projectId),
    queryFn: () => listRuns({ projectId, pageSize: 20 }),
  });

  const designs = React.useMemo(
    () => (artifactsQ.data ?? []).filter((a) => DESIGN_TYPES.includes(a.type)),
    [artifactsQ.data],
  );

  const requirementStories = React.useMemo(
    () => (reqStoriesQ.data ?? []).filter((a) => a.type === "story"),
    [reqStoriesQ.data],
  );

  // ADO source key for a story = body.traceability.jiraIssueKey.
  const storyRef = React.useCallback(
    (a: Artifact): string | undefined =>
      a.body.kind === "story" ? a.body.traceability?.jiraIssueKey : undefined,
    [],
  );

  // Ref + title of every requirements story — lightweight index for the agent.
  const allStories = React.useMemo(
    () =>
      requirementStories
        .map((s) =>
          s.body.kind === "story"
            ? {
                ref: storyRef(s),
                title: s.body.title,
                // The board's own type. Without it an Epic and three board-setup
                // Tasks reach the Design agent indistinguishable from user stories,
                // and it designs a system for whatever it is handed.
                type: s.body.workItemType || undefined,
              }
            : null,
        )
        .filter(
          (s): s is { ref: string | undefined; title: string; type: string | undefined } =>
            !!s,
        ),
    [requirementStories, storyRef],
  );

  // Full content of each story (AC given/when/then rows flattened to lines) so the
  // design agent has everything it needs to design from without re-fetching the board.
  const allStoryContent = React.useMemo(
    () =>
      requirementStories
        .map((a) => {
          if (a.body.kind !== "story") return null;
          const ac = (a.body.acceptanceCriteria ?? [])
            .map((c) =>
              [
                c.given && `Given ${c.given}`,
                c.when && `When ${c.when}`,
                c.then && `Then ${c.then}`,
              ]
                .filter(Boolean)
                .join(" "),
            )
            .filter(Boolean);
          return {
            ref: storyRef(a),
            title: a.body.title,
            description: a.body.description,
            acceptance_criteria: ac,
          };
        })
        .filter((s): s is NonNullable<typeof s> => !!s),
    [requirementStories, storyRef],
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

  const deletion = useDeleteArtifact(projectId, {
    onDeleted: (a) => {
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
  });

  // Chat drawer — talk directly to the Design agent. It consumes the imported user
  // stories through `context.requirements` (the backend formats pipeline_context
  // .requirements into the agent input).
  const [chatOpen, setChatOpen] = React.useState(false);
  const [agentModel, setAgentModel] = React.useState<string>();
  // Design source: "requirements" threads the approved stories into the agent's
  // context; "freeform" sends none, so the agent designs purely from the chat
  // prompt (a deliberate standalone choice, not just "no stories exist").
  const [designSource, setDesignSource] = React.useState<"requirements" | "freeform">(
    "requirements",
  );
  const fromRequirements = designSource === "requirements";
  const chat = useAgentChat({
    agent: "design",
    projectId,
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
      // Freeform mode omits requirements entirely → the agent works standalone.
      requirements: fromRequirements
        ? {
            all_stories: allStories,
            selected_stories: allStoryContent,
          }
        : undefined,
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
            <div
              className="inline-flex items-center rounded-md border p-0.5 text-xs"
              role="group"
              aria-label="Design source"
            >
              <button
                type="button"
                aria-pressed={fromRequirements}
                onClick={() => setDesignSource("requirements")}
                className={cn(
                  "rounded px-2 py-1 font-medium transition-colors",
                  fromRequirements
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                From requirements
                {requirementStories.length > 0 && (
                  <span className="ml-1 opacity-70">{requirementStories.length}</span>
                )}
              </button>
              <button
                type="button"
                aria-pressed={!fromRequirements}
                onClick={() => setDesignSource("freeform")}
                className={cn(
                  "rounded px-2 py-1 font-medium transition-colors",
                  !fromRequirements
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                Freeform
              </button>
            </div>
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
          <ArtifactList
            items={artifactsQ.isLoading ? null : designs}
            selectedId={selected?.id}
            onSelect={selectArtifact}
            onDelete={deletion.onDelete}
            deletingId={deletion.deletingId}
            isLoading={artifactsQ.isLoading}
            emptyTitle="No design artifacts yet"
            emptyDescription="Trigger the Design agent once Requirements are approved."
          />
          {deletion.dialog}
        </aside>

        <main className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex-1 overflow-auto">
            {/* Generated documents — anything the chat produces surfaces here on the
                MAIN screen, per the self-contained agent-page design language. */}
            {chat.documents.length > 0 && (
              <div className="mx-auto max-w-5xl px-4 pt-4 md:px-6">
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

                <ArtifactViewer artifact={selected} />

                <ApprovalCard
                  status={selected.status}
                  title="Gate: Architect review"
                  description="Approval accepts the design and unlocks Development."
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
                      ? () =>
                          decisionMutation.mutate({ id: selected.id, status: "approved" })
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

function ArtifactViewer({ artifact }: { artifact: Artifact }) {
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

