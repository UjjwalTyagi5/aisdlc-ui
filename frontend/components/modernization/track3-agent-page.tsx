"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderGit2, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { GeneratedDocuments } from "@/components/app/generated-documents";
import { ModelSelector } from "@/components/app/model-selector";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useChatDeepLink } from "@/hooks/use-chat-deep-link";
import { PHASE_LABEL } from "@/lib/agents";
import { listStageVersions, type ArtifactVersionDetail } from "@/lib/api/artifact-versions";
import type { Track3Stage } from "@/lib/api/modernization";
import { getProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { TRACK_META, trackHasAgent } from "@/lib/tracks";
import type { ProjectId } from "@/lib/schemas";
import type { LegacyCodeRecord } from "@/lib/schemas/modernization";

import { HowItWorks, type HowItWorksStep } from "./how-it-works";
import {
  LegacyCodeStatus,
  PullLegacyCodeDialog,
  useAnnouncePullOutcome,
  useLegacyCode,
} from "./legacy-code-control";
import { VersionHistory } from "./version-history";
import { VersionView } from "./version-view";

/**
 * The shell both of Track 3's first agent pages share.
 *
 *   header   the agent, its model, the project's pulled legacy code (and Pull legacy
 *            code), and the button that opens the agent's chat.
 *   left     every brief / assessment the agent has recorded, newest first — each a
 *            frozen version (`artifact_versions`), so an old one reads exactly as it was.
 *   center   "how it works" until a version is opened; then that version, with its
 *            Word/PDF download and the Sign-off.
 *
 * THE PAGE OPENS ON THE GUIDE, not on the newest version: what the agent recorded is
 * history to open, not a page that is already filled in. A version recorded while the
 * page is open is the exception — it opens by itself, so the user sees what they just
 * produced.
 *
 * The chat talks to the agent's OWN socket (`agent` is the backend id), never the
 * Orchestrator's: this is the standalone mode. The backend refuses a project whose
 * track does not include the agent; this page says so first rather than opening a chat
 * that would only ever answer with that refusal.
 */
export type GuideActions = {
  pull: () => void;
  run: () => void;
  legacy: LegacyCodeRecord | undefined;
  legacyStatus: React.ReactNode;
};

export function Track3AgentPage({
  phase,
  runLabel,
  intro,
  noun,
  historyTitle,
  guideTitle,
  guide,
  renderVersion,
}: {
  phase: Track3Stage;
  runLabel: string;
  intro: string;
  /** "brief" / "assessment" — how one version is named on the page. */
  noun: string;
  historyTitle: string;
  guideTitle: string;
  guide: (actions: GuideActions) => HowItWorksStep[];
  renderVersion: (payload: unknown, detail: ArtifactVersionDetail) => React.ReactNode;
}) {
  const params = useParams<{ id: string }>();
  const projectId = params.id as ProjectId;
  const queryClient = useQueryClient();
  const projectQ = useQuery({ queryKey: qk.projects.detail(projectId), queryFn: () => getProject(projectId) });

  const [agentModel, setAgentModel] = React.useState<string>();
  const [chatOpen, setChatOpen] = React.useState(false);
  const [pullOpen, setPullOpen] = React.useState(false);
  const [selected, setSelected] = React.useState<number | null>(null);
  const linkedSession = useChatDeepLink(setChatOpen);

  const legacyQ = useLegacyCode(projectId, phase);
  useAnnouncePullOutcome(legacyQ.data);

  const versionsKey = qk.artifactVersions.forStage(projectId, phase);
  const versionsQ = useQuery({ queryKey: versionsKey, queryFn: () => listStageVersions(projectId, phase) });

  const refresh = React.useCallback(() => {
    queryClient.invalidateQueries({ queryKey: qk.artifactVersions.forStage(projectId, phase) });
    queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
    // Discovery's clone tool can replace the project's pulled code mid-conversation.
    queryClient.invalidateQueries({ queryKey: qk.modernization.legacyCode(projectId) });
  }, [queryClient, projectId, phase]);

  const chat = useAgentChat({
    openSessionId: linkedSession,
    agent: phase,
    projectId,
    offeringId: agentModel,
    onArtifact: refresh,
    context: { project_id: projectId, page: PHASE_LABEL[phase] },
  });

  // A turn that recorded a brief or an assessment emits no artifact event — the record
  // is a version, not a file — so the list refreshes when a turn ends.
  const wasBusy = React.useRef(false);
  React.useEffect(() => {
    if (wasBusy.current && !chat.busy) refresh();
    wasBusy.current = chat.busy;
  }, [chat.busy, refresh]);

  // Open a version recorded while this page is open; leave the guide up on arrival.
  const newestSeen = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (!versionsQ.data) return;
    const newest = versionsQ.data.reduce((max, v) => Math.max(max, v.version), 0);
    if (newestSeen.current !== null && newest > newestSeen.current) setSelected(newest);
    newestSeen.current = Math.max(newestSeen.current ?? 0, newest);
  }, [versionsQ.data]);

  if (projectQ.isLoading) {
    return <div className="w-full p-4 md:px-10 md:py-8"><LoadingState variant="card" /></div>;
  }
  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState title="Project not found" description="This project could not be loaded." onRetry={() => projectQ.refetch()} />
      </div>
    );
  }
  const project = projectQ.data;
  if (!trackHasAgent(project.track, phase)) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <EmptyState
          title={`${PHASE_LABEL[phase]} is a Code Modernization agent`}
          description={`This project is on ${TRACK_META[project.track].label} (Track ${TRACK_META[project.track].number}), whose roster does not include it.`}
        />
      </div>
    );
  }

  const legacy = legacyQ.data;
  const pulling = legacy?.status === "pulling";
  const legacyStatus = <LegacyCodeStatus record={legacy} />;
  const steps = guide({ pull: () => setPullOpen(true), run: () => setChatOpen(true), legacy, legacyStatus });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-2xl space-y-1">
            <h1 className="text-xl font-semibold tracking-tight">{PHASE_LABEL[phase]}</h1>
            <p className="text-muted-foreground text-xs">{intro}</p>
            {legacyStatus}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label={`${PHASE_LABEL[phase]} agent model`}
              projectId={projectId}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button size="sm" variant="outline" onClick={() => setPullOpen(true)} disabled={pulling}>
              <FolderGit2 className="size-4" aria-hidden />
              {legacy?.pull ? "Pull again" : "Pull legacy code"}
            </Button>
            <Button size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />
              {runLabel}
            </Button>
          </div>
        </div>
      </div>

      <div className="grid flex-1 gap-0 overflow-hidden md:grid-cols-[280px_1fr]">
        <aside className="flex min-h-0 flex-col gap-4 overflow-auto border-b p-3 md:border-r md:border-b-0">
          <VersionHistory
            title={historyTitle}
            noun={noun}
            versions={versionsQ.data}
            isLoading={versionsQ.isLoading}
            isError={versionsQ.isError}
            onRetry={() => versionsQ.refetch()}
            selected={selected}
            onSelect={setSelected}
          />
        </aside>
        <main className="min-h-0 overflow-auto p-4 md:p-6">
          {chat.documents.length > 0 && (
            <GeneratedDocuments
              documents={chat.documents}
              projectId={projectId}
              stage={phase}
              artifacts={null}
              className="mb-6 rounded-lg border bg-muted/20 p-3"
            />
          )}
          {selected === null ? (
            <HowItWorks title={guideTitle} steps={steps} />
          ) : (
            <VersionView
              key={selected}
              projectId={projectId}
              stage={phase}
              noun={noun}
              version={selected}
              render={renderVersion}
            />
          )}
        </main>
      </div>

      <PullLegacyCodeDialog
        projectId={projectId}
        stage={phase}
        open={pullOpen}
        onOpenChange={setPullOpen}
        current={legacy}
      />
      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: PHASE_LABEL[phase] }}
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
