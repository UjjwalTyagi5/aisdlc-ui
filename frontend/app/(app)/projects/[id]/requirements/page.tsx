"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, MessageSquare, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { Textarea } from "@/components/ui/textarea";

import { AcEditor, type AcRow } from "@/components/app/ac-editor";
import { ActivityTimeline } from "@/components/app/activity-timeline";
import { ModelSelector } from "@/components/app/model-selector";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { ApprovalCard } from "@/components/app/approval-card";
import { ArtifactList } from "@/components/app/artifact-list";
import {
  InlineCommentThread,
  type InlineComment,
} from "@/components/app/inline-comment-thread";
import { TraceabilityPanel } from "@/components/app/traceability-panel";
import { RequireRole } from "@/components/auth/require-role";

import { useSession } from "@/hooks/use-session";
import { BoardProjectDialog } from "@/components/app/board-project-dialog";
import { listArtifacts, updateArtifact } from "@/lib/api/artifacts";
import { getProject, getBoardProjects } from "@/lib/api/projects";
import { getRunSteps, listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type {
  Artifact,
  ArtifactId,
  ProjectId,
  Step,
  UserRef,
} from "@/lib/schemas";

export default function RequirementsPage() {
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

  // Fetches
  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });
  const artifactsQ = useQuery({
    queryKey: qk.artifacts.forProject(projectId),
    queryFn: () => listArtifacts(projectId, { phase: "requirements" }),
  });
  const runsQ = useQuery({
    queryKey: qk.runs.forProject(projectId),
    queryFn: () => listRuns({ projectId, pageSize: 20 }),
  });
  // The finalized ADO board project (what the user already pulled from). Lets the
  // chat know the project without asking.
  const boardProjectsQ = useQuery({
    queryKey: ["board-projects", projectId],
    queryFn: () => getBoardProjects(projectId),
    staleTime: 60_000,
  });
  const boardProject = boardProjectsQ.data?.selected ?? undefined;

  const stories = React.useMemo(
    () => (artifactsQ.data ?? []).filter((a) => a.type === "story"),
    [artifactsQ.data],
  );

  // Selection is DECOUPLED: the round checkboxes hold the CHAT SCOPE (which stories the
  // agent works on); the detail card is a separate VIEW driven by ?artifact=. Row click
  // opens/closes the detail; checkboxes add/remove chat scope. Nothing checked ⇒ the chat
  // receives no story context.
  const [selectedStoryIds, setSelectedStoryIds] = React.useState<Set<string>>(
    () => new Set(),
  );

  // Active detail card — driven ONLY by ?artifact=<id> (set on click). No auto-select:
  // the panel stays blank until the user opens a story.
  const selectedFromUrl = searchParams.get("artifact");
  const selected = React.useMemo(
    // Explicit selection only — no auto-select. The detail panel stays blank until
    // the user clicks a story (which sets ?artifact=). Checking a story's box scopes
    // the chat but does NOT open it in the detail panel.
    () => (selectedFromUrl ? stories.find((s) => s.id === selectedFromUrl) ?? null : null),
    [stories, selectedFromUrl],
  );

  const setActiveArtifact = React.useCallback(
    (a: Artifact) => {
      const next = new URLSearchParams(searchParams);
      next.set("artifact", a.id);
      router.replace(`/projects/${projectId}/requirements?${next.toString()}`);
    },
    [router, projectId, searchParams],
  );

  // Row click = VIEW only, and it TOGGLES: opens the story's detail, or clears it if it's
  // already open (so the panel can be emptied). Does NOT scope the chat.
  const selectArtifact = React.useCallback(
    (a: Artifact) => {
      const next = new URLSearchParams(searchParams);
      if (next.get("artifact") === a.id) next.delete("artifact");
      else next.set("artifact", a.id);
      router.replace(`/projects/${projectId}/requirements?${next.toString()}`);
    },
    [router, projectId, searchParams],
  );

  // Round checkbox = CHAT SCOPE only (which stories the agent works on). Does NOT change
  // the viewed detail. Nothing checked ⇒ no story context is sent to the chat.
  const toggleStorySelect = React.useCallback(
    (a: Artifact) => {
      setSelectedStoryIds((cur) => {
        const next = new Set(cur);
        if (next.has(a.id)) next.delete(a.id);
        else next.add(a.id);
        return next;
      });
    },
    [],
  );

  // Refs the chat scopes to. ADO source key = body.traceability.jiraIssueKey.
  const storyRef = React.useCallback(
    (a: Artifact): string | undefined =>
      a.body.kind === "story" ? a.body.traceability?.jiraIssueKey : undefined,
    [],
  );
  // The story IDs the chat is scoped to: ONLY the explicitly-checked stories. When
  // nothing is checked we pass an empty scope (blank) — the agent then works from the
  // user's prompt instead of auto-fetching a story they didn't select.
  const scopeIds = React.useMemo(() => selectedStoryIds, [selectedStoryIds]);
  const selectedStoryRefs = React.useMemo(
    () =>
      stories
        .filter((s) => scopeIds.has(s.id))
        .map(storyRef)
        .filter((r): r is string => !!r),
    [stories, scopeIds, storyRef],
  );

  // Full content of each story so the chat knows what it's working on without
  // re-fetching the board. AC rows ({given,when,then}) flatten to readable lines.
  const storyContent = React.useCallback(
    (a: Artifact) => {
      if (a.body.kind !== "story") return null;
      const ac = (a.body.acceptanceCriteria ?? [])
        .map((c) =>
          [c.given && `Given ${c.given}`, c.when && `When ${c.when}`, c.then && `Then ${c.then}`]
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
    },
    [storyRef],
  );
  const selectedStories = React.useMemo(
    () =>
      stories
        .filter((s) => scopeIds.has(s.id))
        .map(storyContent)
        .filter((s): s is NonNullable<typeof s> => !!s),
    [stories, scopeIds, storyContent],
  );

  // Chat drawer — streaming agent chat via /api/chat
  const [chatOpen, setChatOpen] = React.useState(false);
  const chat = useAgentChat({
    // Talk directly to the Requirements agent's WS (self-contained agent page),
    // not the orchestrator — so the selected/all story refs in pipeline_context
    // actually reach the agent (the orchestrator rebuilds context and drops them).
    agent: "requirement",
    projectId,
    // Switching board projects (re-pull a different one) starts a fresh chat
    // thread scoped to the new project instead of carrying old-project history.
    sessionKey: boardProject,
    // When the agent writes a board item or generates a document, refresh the
    // main-screen lists so chat output shows up in the RED region.
    onArtifact: () => {
      queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      queryClient.invalidateQueries({ queryKey: qk.runs.forProject(projectId) });
    },
    context: {
      // Lets the backend resolve this project's per-stage MCP selection and bind
      // those tools for the requirements agent in chat.
      project_id: projectId,
      page: "Requirements",
      requirements: {
        board_project: boardProject,
        // ONLY the checked stories are sent. Nothing checked ⇒ empty ⇒ no story context,
        // so the agent works from the user's prompt instead of every story on the board.
        selected_story_refs: selectedStoryRefs,
        selected_stories: selectedStories,
      },
    },
  });

  // "Pull stories" → open the board-project picker, then ingest the chosen one.
  const [boardPickerOpen, setBoardPickerOpen] = React.useState(false);
  // Agent model choice for this page. Local for now. TODO(byok-model): pass to the
  // requirements run / ingestion dispatch and persist as the project default.
  const [agentModel, setAgentModel] = React.useState<string>();
  const onIngested = React.useCallback(() => {
    queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
    queryClient.invalidateQueries({ queryKey: qk.runs.forProject(projectId) });
    // The chosen board project may have changed — refresh it so the chat context
    // reflects the project just pulled, and drop selections that referenced the
    // previous project's (now-replaced) stories.
    queryClient.invalidateQueries({ queryKey: ["board-projects", projectId] });
    setSelectedStoryIds(new Set());
  }, [queryClient, projectId]);

  // Rejection UX is inside ApprovalCard already; we just wire the handler here.

  // Mutations
  const saveMutation = useMutation({
    mutationFn: async (input: {
      id: ArtifactId;
      patch: Parameters<typeof updateArtifact>[1];
    }) => updateArtifact(input.id, input.patch),
    onMutate: async ({ id, patch }) => {
      await queryClient.cancelQueries({
        queryKey: qk.artifacts.forProject(projectId),
      });
      const previous = queryClient.getQueryData<Artifact[]>(
        qk.artifacts.forProject(projectId),
      );
      if (previous) {
        queryClient.setQueryData<Artifact[]>(
          qk.artifacts.forProject(projectId),
          previous.map((a) =>
            a.id === id
              ? {
                  ...a,
                  title: patch.title ?? a.title,
                  status: patch.status ?? a.status,
                  body: patch.body ?? a.body,
                  updatedAt: new Date().toISOString(),
                }
              : a,
          ),
        );
      }
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) {
        queryClient.setQueryData(qk.artifacts.forProject(projectId), ctx.previous);
      }
      toast.error("Couldn't save", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
    },
  });

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

  // Per-story edit buffer so updates feel instant
  const [buffer, setBuffer] = React.useState<{
    id: string;
    title: string;
    description: string;
    ac: AcRow[];
  } | null>(null);

  React.useEffect(() => {
    if (!selected || selected.body.kind !== "story") {
      setBuffer(null);
      return;
    }
    setBuffer({
      id: selected.id,
      title: selected.body.title,
      description: selected.body.description,
      ac: selected.body.acceptanceCriteria.map((r) => ({ ...r })),
    });
  }, [selected?.id, selected?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const flushBuffer = React.useCallback(() => {
    if (!selected || !buffer || selected.body.kind !== "story") return;
    const dirty =
      buffer.title !== selected.body.title ||
      buffer.description !== selected.body.description ||
      JSON.stringify(buffer.ac) !== JSON.stringify(selected.body.acceptanceCriteria);
    if (!dirty) return;
    saveMutation.mutate({
      id: selected.id,
      patch: {
        title: buffer.title,
        body: {
          kind: "story",
          title: buffer.title,
          description: buffer.description,
          acceptanceCriteria: buffer.ac,
          traceability: selected.body.traceability,
        },
      },
    });
  }, [buffer, selected, saveMutation]);

  // Debounced autosave (600ms)
  React.useEffect(() => {
    if (!buffer) return;
    const t = setTimeout(() => flushBuffer(), 600);
    return () => clearTimeout(t);
  }, [buffer, flushBuffer]);

  // Keyboard shortcuts — j/k navigate, a approve, r reject, c toggles chat.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      if (el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (stories.length === 0) return;

      const idx = selected ? stories.findIndex((s) => s.id === selected.id) : 0;
      if (e.key === "j") {
        e.preventDefault();
        const next = stories[Math.min(stories.length - 1, idx + 1)];
        if (next) setActiveArtifact(next);
      } else if (e.key === "k") {
        e.preventDefault();
        const prev = stories[Math.max(0, idx - 1)];
        if (prev) setActiveArtifact(prev);
      } else if (e.key === "a" && selected?.status === "awaiting_approval") {
        e.preventDefault();
        decisionMutation.mutate({ id: selected.id, status: "approved" });
      } else if (e.key === "r" && selected?.status === "awaiting_approval") {
        e.preventDefault();
        // Defer reason to the card's inline textarea
        const cardReject = document.querySelector<HTMLButtonElement>(
          '[data-testid="approval-reject"]',
        );
        cardReject?.click();
      } else if (e.key === "c") {
        e.preventDefault();
        setChatOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stories, selected, setActiveArtifact, decisionMutation]);

  const selectedRun = React.useMemo(() => {
    if (!selected) return null;
    return runsQ.data?.items.find((r) => r.id === selected.runId) ?? null;
  }, [runsQ.data, selected]);

  const stepsQ = useQuery({
    queryKey: selectedRun ? qk.runs.steps(selectedRun.id) : ["no-run"],
    queryFn: () => getRunSteps(selectedRun!.id),
    enabled: !!selectedRun,
  });

  // ---------- render ----------
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
      {/* Header / action bar */}
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Requirements</h1>
            <p className="text-muted-foreground text-xs">
              {stories.length} {stories.length === 1 ? "story" : "stories"} · signed in as{" "}
              <span className="text-foreground font-mono">{role}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label="Requirements agent model"
              projectId={projectId}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />
              Run Requirements agent
              <kbd className="bg-muted ml-1 rounded border px-1 font-mono text-[10px]">C</kbd>
            </Button>
            <RequireRole capability="run:trigger">
              <Button variant="ghost" size="sm" onClick={() => setBoardPickerOpen(true)}>
                <Play className="size-4" aria-hidden />
                Pull stories
              </Button>
            </RequireRole>
          </div>
        </div>
      </div>

      {/* Split content */}
      <div className="grid flex-1 gap-0 overflow-hidden md:grid-cols-[340px_1fr] xl:grid-cols-[360px_1fr]">
        {/* Left — artifact list */}
        <aside
          aria-label="Stories"
          className="flex min-h-0 flex-col overflow-auto border-b p-3 md:border-b-0 md:border-r"
        >
          <ArtifactList
            items={artifactsQ.isLoading ? null : stories}
            selectedId={selected?.id}
            onSelect={selectArtifact}
            selectedIds={selectedStoryIds}
            onToggleSelect={toggleStorySelect}
            isLoading={artifactsQ.isLoading}
            emptyTitle="No stories yet"
            emptyDescription='Click "Pull stories" to pull structured stories from Azure DevOps.'
          />
        </aside>

        {/* Right — story detail */}
        <main className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex-1 overflow-auto">
            {/* Generated documents — anything the chat (BLUE) produces shows here on
                the MAIN screen (RED), per the agent-page design language. */}
            {chat.documents.length > 0 && (
              <div className="mx-auto max-w-3xl px-4 pt-4 md:px-6">
                <section className="rounded-lg border bg-muted/20 p-3">
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
            {selected && buffer && selected.body.kind === "story" ? (
              <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
                {/* Title + description */}
                <section className="space-y-3">
                  <Label htmlFor="story-title" className="sr-only">
                    Story title
                  </Label>
                  <Input
                    id="story-title"
                    value={buffer.title}
                    onChange={(e) => setBuffer({ ...buffer, title: e.target.value })}
                    className="text-lg font-semibold"
                  />
                  <Textarea
                    aria-label="Story description"
                    rows={3}
                    value={buffer.description}
                    onChange={(e) =>
                      setBuffer({ ...buffer, description: e.target.value })
                    }
                  />
                  <p className="text-muted-foreground text-xs">
                    {saveMutation.isPending
                      ? "Saving…"
                      : `v${selected.version} · last saved ${new Date(
                          selected.updatedAt,
                        ).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
                  </p>
                </section>

                {/* AC editor */}
                <section className="space-y-2">
                  <h3 className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                    Acceptance criteria
                  </h3>
                  <AcEditor
                    value={buffer.ac}
                    onChange={(next) => setBuffer({ ...buffer, ac: next })}
                  />
                </section>

                {/* Traceability */}
                <TraceabilityPanel
                  trace={selected.body.traceability ?? {}}
                  projectId={projectId}
                />

                {/* Approval */}
                <ApprovalCard
                  status={selected.status}
                  title="Gate: PM review"
                  description="Approval unlocks the Design phase for this story."
                  decidedBy={selected.status === "approved" || selected.status === "rejected" ? me : undefined}
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
                  onRetry={() => {
                    toast.message("Retry wires to the run orchestrator in Chunk 15");
                  }}
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

                {/* Comments */}
                <section className="space-y-3">
                  <h3 className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                    Comments
                  </h3>
                  <InlineCommentThread
                    comments={DEMO_COMMENTS}
                    currentUser={me}
                    onReply={async () => {
                      toast.message("Comments wire to the real thread API in Chunk 15");
                    }}
                  />
                </section>
              </div>
            ) : (
              <EmptyState
                title="Select a story"
                description="Pick a story from the list to view and edit it."
                variant="plain"
                className="mt-10"
              />
            )}
          </div>

          {/* Bottom dock — activity timeline for selected story */}
          {selected && (
            <div className="max-h-48 overflow-auto border-t bg-muted/30 px-4 py-3 md:px-6">
              <h3 className="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wider">
                Activity
              </h3>
              <ActivityTimeline
                steps={(stepsQ.data ?? []) as Step[]}
                className="max-w-3xl"
              />
              {stepsQ.data?.length === 0 && (
                <p className="text-muted-foreground text-xs">No steps recorded yet.</p>
              )}
            </div>
          )}
        </main>
      </div>

      {/* Board-project picker — "Run Requirements agent" */}
      <BoardProjectDialog
        open={boardPickerOpen}
        onOpenChange={setBoardPickerOpen}
        projectId={projectId}
        onIngested={onIngested}
      />

      {/* Agent chat drawer */}
      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{
          page: "Requirements",
          // Only show a story chip when stories are actually checked — not the active
          // detail story. Matches the (now blank) scope passed to the agent, so the
          // header doesn't imply a story is bound when none is selected.
          artifactTitle:
            selectedStoryRefs.length > 1
              ? `${selectedStoryRefs.length} stories scoped`
              : selectedStoryRefs[0],
        }}
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

// Placeholder comments until Chunk 15 wires the real thread API.
const DEMO_COMMENTS: InlineComment[] = [
  {
    id: "c1",
    author: "agent",
    body: "I split the original ticket into three stories — reply /merge to consolidate.",
    createdAt: new Date(Date.now() - 1000 * 60 * 45).toISOString(),
    severity: "suggestion",
  },
];
