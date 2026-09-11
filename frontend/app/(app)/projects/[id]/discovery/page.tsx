"use client";

import { ErrorState } from "@/components/ui/error-state";
import { AssessmentView } from "@/components/modernization/assessment-view";
import { Track3AgentPage } from "@/components/modernization/track3-agent-page";
import { DiscoveryAssessment } from "@/lib/schemas/modernization";

/**
 * Discovery & Assessment — Track 3 (Code Modernization), PRD §23.2.
 *
 * The assessment is data (modules, scores, tiers, a dependency graph, flags), not a
 * document, so it is shown as data. Every assessment the agent records is a new
 * version on the left; accepting one as the planning baseline is its Sign-off. The
 * legacy code is the project's one pulled checkout — the same the Requirements agent
 * read — so "Pull legacy code" here and there fill the same thing.
 */
export default function DiscoveryPage() {
  return (
    <Track3AgentPage
      phase="discovery"
      runLabel="Run Discovery & Assessment"
      intro="Reads the legacy repository read-only, maps its dependency graph, flags end-of-life and vulnerable dependencies, and scores every module for migration risk."
      noun="assessment"
      historyTitle="Assessments"
      guideTitle="How an assessment gets made"
      guide={({ pull, run, legacy, legacyStatus }) => [
        {
          title: "Pull the legacy code",
          body: "Choose the legacy repository — or use the one already pulled on the Requirements page; it is the same checkout. It is cloned read-only and never changed.",
          status: legacyStatus,
          action: {
            label: legacy?.pull ? "Pull again" : "Pull legacy code",
            onClick: pull,
            disabled: legacy?.status === "pulling",
          },
        },
        {
          title: "Run Discovery & Assessment",
          body: "It maps the modules and their dependencies, flags end-of-life, deprecated and vulnerable dependencies, and scores every module for migration risk — mechanical, LLM-assisted or manual-only. It uses the target stack from the migration-intent brief.",
          action: { label: "Run Discovery & Assessment", onClick: run },
        },
        {
          title: "Open the assessment",
          body: "Each assessment appears on the left as a new version. Open one to explore its modules and flags, download the report as Word or PDF, and accept it as the planning baseline — the BA or a Project Admin approves it, never the person who produced it.",
        },
      ]}
      renderVersion={(payload) => {
        const parsed = DiscoveryAssessment.safeParse(payload);
        return parsed.success ? (
          <AssessmentView assessment={parsed.data} />
        ) : (
          <ErrorState
            title="This assessment could not be read"
            description="Its saved shape is not one this page understands."
          />
        );
      }}
    />
  );
}
