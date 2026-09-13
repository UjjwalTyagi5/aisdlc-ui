import * as React from "react";

import type { MigrationIntentBrief } from "@/lib/schemas/modernization";

import { sectionPlan, type BriefSection } from "./brief-display";
import {
  BriefHero,
  ChangeAtAGlance,
  Constraints,
  ModuleChanges,
  People,
  RecommendedStack,
  RepositoryLine,
  RisksAndQuestions,
  ScopeView,
  Section,
  Success,
  Timeline,
  TradeOffs,
  WhyNow,
} from "./brief-sections";

/**
 * The migration-intent brief, laid out to be read at a glance: a title band with the
 * goal and key facts, then numbered sections — what changes in each part of the
 * system (coloured by support status), why, the recommended target and its trade-offs,
 * scope, the change per module, the timeline, constraints and how success is measured.
 *
 * The same sections, in the same order, as the Word and PDF downloads
 * (`requirements_modernization_agent/brief_document.py`). A version-1 brief renders
 * through the same view: its plain drivers are tagged by their words, and today →
 * target becomes one row.
 */
const TITLES: Record<BriefSection, string> = {
  glance: "The change at a glance",
  why: "Why we are modernizing",
  recommendation: "Recommended target stack",
  target_state: "Target state",
  scope: "Scope",
  modules: "What changes in each module",
  tradeoffs: "Trade-offs",
  timeline: "Timeline",
  constraints: "Constraints",
  success: "How we will measure success",
  people: "Stakeholders",
  risks: "Assumptions, risks and open questions",
};

function body(key: BriefSection, brief: MigrationIntentBrief): React.ReactNode {
  switch (key) {
    case "glance": return <ChangeAtAGlance brief={brief} />;
    case "why": return <WhyNow brief={brief} />;
    case "recommendation": return <RecommendedStack brief={brief} />;
    case "target_state": return <p className="text-[14px] leading-relaxed">{brief.target_state.description}</p>;
    case "scope": return <ScopeView brief={brief} />;
    case "modules": return <ModuleChanges brief={brief} />;
    case "tradeoffs": return <TradeOffs brief={brief} />;
    case "timeline": return <Timeline brief={brief} />;
    case "constraints": return <Constraints brief={brief} />;
    case "success": return <Success brief={brief} />;
    case "people": return <People brief={brief} />;
    case "risks": return <RisksAndQuestions brief={brief} />;
  }
}

export function MigrationBriefCard({
  brief,
  updatedAt,
}: {
  brief: MigrationIntentBrief;
  updatedAt?: string | null;
}) {
  // The brief's own time. `updatedAt` is its RUN's last change, which moves whenever
  // anything else writes to that run (Discovery, in the same Orchestrator chat).
  const recordedAt = brief.recorded_at ?? updatedAt;
  const plan = sectionPlan(brief);
  return (
    <article className="space-y-9" aria-label="Migration-intent brief">
      <BriefHero brief={brief} recordedAt={recordedAt} />
      {plan.map((key, i) => (
        <Section key={key} n={i + 1} title={TITLES[key]} id={`brief-${key}`}>
          {body(key, brief)}
        </Section>
      ))}
      <RepositoryLine brief={brief} />
    </article>
  );
}
