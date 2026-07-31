"use client";

import { StageWorkbench } from "@/components/app/stage-workbench";

/**
 * Discovery & Assessment agent — PRD §23.2 (Track 3) and §24.2 (Track 4).
 * Owner: Architect. Accepting the assessment is a Sign-off; it becomes the
 * planning baseline every later agent inherits.
 */
export default function DiscoveryPage() {
  return (
    <StageWorkbench
      phase="discovery"
      agent="discovery"
      title="Discovery & Assessment"
      runLabel="Run Discovery & Assessment agent"
      emptyTitle="No assessment yet"
      emptyDescription="Modernization: clones and reads the legacy repo as the source of truth — detects languages, frameworks and build chain, maps the dependency graph, flags EOL libraries, dead code and security red flags, and scores each module for migration risk. RPA & infrastructure: parses an exported bot package or an infrastructure inventory and assesses each item's disposition (retain, resize, re-platform, retire)."
    />
  );
}
