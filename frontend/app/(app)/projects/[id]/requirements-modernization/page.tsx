"use client";

import { ErrorState } from "@/components/ui/error-state";
import { MigrationBriefCard } from "@/components/modernization/migration-brief-card";
import { Track3AgentPage } from "@/components/modernization/track3-agent-page";
import { MigrationIntentBrief } from "@/lib/schemas/modernization";

/**
 * Requirements in migration-intent mode — Track 3 (Code Modernization), PRD §23.1.
 * Owner: BA. Its own agent, not Portfolio 1's Requirements with a flag: it captures
 * why the modernization is happening, from what to what, scope, constraints and
 * success criteria — a brief, not a story backlog.
 *
 * The journey: pull the legacy code → run the agent, which reads the code and then asks
 * what the code cannot tell it → answer → the brief is recorded as a new version on the
 * left, where it is opened, downloaded and signed off.
 */
export default function RequirementsModernizationPage() {
  return (
    <Track3AgentPage
      phase="requirements_modernization"
      runLabel="Run Requirements agent"
      intro="Captures the migration intent: why the modernization is happening, what the system runs on today and what it should run on, scope, constraints and success criteria."
      noun="brief"
      historyTitle="Briefs"
      guideTitle="How a migration-intent brief gets made"
      guide={({ pull, run, legacy, legacyStatus }) => [
        {
          title: "Pull the legacy code",
          body: "Choose the legacy repository. It is cloned read-only for this project, so the agent reads today's system before it asks you anything — and Discovery & Assessment assesses the same code.",
          status: legacyStatus,
          action: {
            label: legacy?.pull ? "Pull again" : "Pull legacy code",
            onClick: pull,
            disabled: legacy?.status === "pulling",
          },
        },
        {
          title: "Run the Requirements agent",
          body: "It tells you what the code shows about today's system — modules, runtimes, versions — and asks you to confirm it. Then it asks what code cannot tell it: why the modernization is happening, the target, scope, constraints and success criteria.",
          action: { label: "Run Requirements agent", onClick: run },
        },
        {
          title: "Answer its questions",
          body: "It asks at most three at a time and records only what you tell it. When every required part is answered, it records the brief.",
        },
        {
          title: "Open the brief",
          body: "Each recorded brief appears on the left as a new version. Open one to read it, download it as Word or PDF, and approve it — the BA or a Project Admin signs it off, never the person who produced it.",
        },
      ]}
      renderVersion={(payload, detail) => {
        const parsed = MigrationIntentBrief.safeParse(payload);
        return parsed.success ? (
          <MigrationBriefCard brief={parsed.data} updatedAt={detail.createdAt ?? null} />
        ) : (
          <ErrorState
            title="This brief could not be read"
            description="Its saved shape is not one this page understands."
          />
        );
      }}
    />
  );
}
