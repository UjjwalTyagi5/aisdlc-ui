"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { PipelineRail, type PipelineRailStage } from "@/components/copilot/pipeline-rail";
import { CopilotChat } from "@/components/copilot/copilot-chat";
import { ArtifactsPanel } from "@/components/copilot/artifacts-panel";
import { useCopilot } from "@/lib/copilot/use-copilot";
import {
  COPILOT_STAGES,
  railStatusFor,
  stageLabel,
  type StageStatusDot,
} from "@/lib/copilot/stages";
import { getRun } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type { RunId } from "@/lib/schemas";

export interface CopilotProps {
  projectId: string;
  runId: string;
}

/**
 * The Orchestrator Copilot shell — three columns: PipelineRail | chat | ArtifactsPanel.
 *
 * The center chat streams stage-attributed agent bubbles, renders inline choice
 * cards + gate states, and drives progression conversationally. State comes from
 * `useCopilot` (the Copilot WS) plus the shared run-detail query for metadata.
 */
export function Copilot({ projectId, runId }: CopilotProps) {
  const {
    messages,
    streaming,
    choiceCard,
    gate,
    activeStage,
    error,
    connState,
    connectionStatus,
    send,
    cancel,
    answerChoice,
    sendGateDecision,
    setStage,
    artifacts,
    openArtifactId,
    setOpenArtifactId,
    streamingArtifactId,
    panelOpen,
    activity,
    working,
    stuck,
    idleSeconds,
  } = useCopilot({ runId, projectId });

  const rid = runId as RunId;
  const runQ = useQuery({
    queryKey: qk.runs.detail(rid),
    queryFn: () => getRun(rid),
    enabled: !!runId,
    refetchInterval: 8_000,
  });

  const [panelCollapsed, setPanelCollapsed] = React.useState(false);

  // Auto-open (uncollapse) the panel the first time an artifact appears — the
  // Claude behavior of surfacing the doc as it starts streaming into the panel.
  const prevPanelOpen = React.useRef(false);
  React.useEffect(() => {
    if (panelOpen && !prevPanelOpen.current) setPanelCollapsed(false);
    prevPanelOpen.current = panelOpen;
  }, [panelOpen]);

  // Derive the active stage's rail status from run status + live gate.
  const activeStatus: StageStatusDot = React.useMemo(() => {
    if (gate?.status === "awaiting_gate") return "awaiting_gate";
    const s = runQ.data?.status;
    if (s === "approved" || s === "merged") return "approved";
    if (s === "rejected" || s === "failed") return "rejected";
    if (choiceCard) return "interviewing";
    if (streaming) return "running";
    // Default: an active but idle-waiting stage is "interviewing" the driver.
    return "interviewing";
  }, [gate?.status, runQ.data?.status, choiceCard, streaming]);

  // Stages that have genuinely produced a persisted artifact — the truth behind
  // "this stage actually ran". Drives the rail's green/Approved dots so jumping
  // straight to Development doesn't paint Requirements/Design as done, and
  // switching back keeps a worked stage green.
  const completedStages = React.useMemo(
    () => new Set(artifacts.map((a) => a.stage)),
    [artifacts],
  );

  const railStages: PipelineRailStage[] = React.useMemo(
    () =>
      COPILOT_STAGES.map((s) => ({
        id: s.id,
        label: s.label,
        status: railStatusFor(s.id, activeStage, activeStatus, completedStages),
        ownerRole: s.ownerRole,
        mandatory: s.mandatory,
      })),
    [activeStage, activeStatus, completedStages],
  );

  const run = runQ.data;

  return (
    <div className="flex h-[calc(100vh-var(--app-header-h,3.5rem))] min-h-0 flex-col">
      {/* Header */}
      <header className="border-line-soft bg-panel-elevated/60 flex items-center gap-3 border-b px-4 py-3 backdrop-blur-sm md:px-6">
        <Button variant="ghost" size="icon" asChild className="-ml-2 size-8 shrink-0">
          <Link href={`/projects/${projectId}`} aria-label="Back to project">
            <ArrowLeft className="size-4" aria-hidden />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="text-brand-bright flex items-center gap-2 font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em]">
            <span className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r bg-clip-text text-transparent">
              ——
            </span>
            Orchestrator Copilot
          </div>
          <h1 className="font-display truncate text-[15px] font-bold leading-tight">
            {/* Run titles come as "<project> · <stage>" — strip any stage tail so the
                live activeStage suffix below doesn't render "… · Design · Design". */}
            {(run?.title ?? "Pipeline run").replace(
              new RegExp(
                `\\s*·\\s*(${COPILOT_STAGES.map((s) => s.label).join("|")})\\s*$`,
                "i",
              ),
              "",
            )}
            <span className="text-muted-foreground ml-2 font-mono text-[11px] font-normal">
              · {stageLabel(activeStage)}
            </span>
          </h1>
        </div>
        {run && <StatusBadge status={run.status} />}
      </header>

      {/* Three-column body */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <PipelineRail
          stages={railStages}
          active={activeStage}
          onSelect={setStage}
          className="hidden w-[240px] shrink-0 md:flex xl:w-[264px]"
        />

        <CopilotChat
          runId={runId}
          messages={messages}
          streaming={streaming}
          working={working}
          onStop={cancel}
          activeStage={activeStage}
          choiceCard={choiceCard}
          gate={gate}
          error={error}
          connState={connState}
          onSend={send}
          onAnswerChoice={answerChoice}
          onGateDecision={sendGateDecision}
          onOpenArtifact={(id) => {
            setPanelCollapsed(false);
            setOpenArtifactId(id);
          }}
        />

        <ArtifactsPanel
          runId={runId}
          activeStage={activeStage}
          gate={gate}
          artifacts={artifacts}
          openArtifactId={openArtifactId}
          onSelectArtifact={setOpenArtifactId}
          streamingArtifactId={streamingArtifactId}
          collapsed={panelCollapsed}
          onToggle={() => setPanelCollapsed((c) => !c)}
          activity={activity}
          working={working}
          stuck={stuck}
          idleSeconds={idleSeconds}
          connectionStatus={connectionStatus}
          className="hidden lg:flex"
        />
      </div>
    </div>
  );
}
