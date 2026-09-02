"use client";

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, MessageSquare, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

import { ActivityTimeline } from "@/components/app/activity-timeline";
import { ModelSelector } from "@/components/app/model-selector";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { ArtifactList } from "@/components/app/artifact-list";
import { TraceabilityPanel } from "@/components/app/traceability-panel";
import { RequireRole } from "@/components/auth/require-role";

import { useSession } from "@/hooks/use-session";
import { useDeleteArtifact } from "@/hooks/use-delete-artifact";
import { BoardProjectDialog } from "@/components/app/board-project-dialog";
import { listArtifacts } from "@/lib/api/artifacts";
import { getProject, getBoardProjects } from "@/lib/api/projects";
import { getRunSteps, listRuns } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type { Artifact, ProjectId, Step } from "@/lib/schemas";

export default function RequirementsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id as ProjectId;
  const searchParams = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { role } = useSession({ required: true });


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

  const deletion = useDeleteArtifact(projectId, {
    onDeleted: (a) => {
      // Drop ?artifact= when the deleted one was open, so a copied link does not point
      // at an artifact that no longer exists.
      if (searchParams.get("artifact") === a.id) {
        const next = new URLSearchParams(searchParams);
        next.delete("artifact");
        const qs = next.toString();
        router.replace(`/projects/${projectId}/requirements${qs ? `?${qs}` : ""}`);
      }
    },
  });

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

  // Keyboard shortcuts — j/k navigate, c toggles chat.
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
      } else if (e.key === "c") {
        e.preventDefault();
        setChatOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stories, selected, setActiveArtifact]);

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
            onDelete={deletion.onDelete}
            deletingId={deletion.deletingId}
            isLoading={artifactsQ.isLoading}
            emptyTitle="No stories yet"
            emptyDescription='Click "Pull stories" to pull structured stories from Azure DevOps.'
          />
          {deletion.dialog}
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
            {selected && selected.body.kind === "story" ? (
              <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
                {/* READ-ONLY. These stories are PULLED FROM the board — the board is
                    the source of truth and this platform has no write-back yet. The
                    fields used to be editable with a 600ms debounced autosave, and
                    every save was a 500: a synthesised story id names no row, and
                    PATCH /artifacts/{id} is an accepted-but-no-op stub regardless. It
                    also printed "last saved HH:MM" from the run's created_at, so it
                    claimed to have saved work it had discarded. */}
                <section className="space-y-3">
                  <h2 className="text-lg font-semibold">{selected.body.title}</h2>
                  {selected.body.description ? (
                    <p className="text-muted-foreground whitespace-pre-wrap text-sm">
                      {selected.body.description}
                    </p>
                  ) : (
                    <p className="text-muted-foreground text-sm italic">
                      No description on the board.
                    </p>
                  )}
                  <p className="text-muted-foreground text-xs">
                    {selected.body.workItemType
                      ? `${selected.body.workItemType} · read-only`
                      : "Read-only"}{" "}
                    — edit this item on the board, or ask the Requirements agent to
                    change it.
                  </p>
                </section>

                <section className="space-y-2">
                  <h3 className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                    Acceptance criteria
                  </h3>
                  {selected.body.acceptanceCriteria.length > 0 ? (
                    <ul className="space-y-1.5">
                      {selected.body.acceptanceCriteria.map((c, i) => (
                        <li
                          key={i}
                          className="text-muted-foreground rounded-md border px-3 py-2 text-sm"
                        >
                          {[
                            c.given && `Given ${c.given}`,
                            c.when && `When ${c.when}`,
                            c.then && `Then ${c.then}`,
                          ]
                            .filter(Boolean)
                            .join(" ") || "—"}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-muted-foreground text-sm italic">
                      None on the board.
                    </p>
                  )}
                </section>

                <TraceabilityPanel
                  trace={selected.body.traceability ?? {}}
                  projectId={projectId}
                />
              </div>
            ) : (
              <EmptyState
                title="Select a story"
                description="Pick a story from the list to view it."
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

