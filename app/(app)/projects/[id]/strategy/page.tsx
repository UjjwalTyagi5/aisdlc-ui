"use client";

import { StageWorkbench } from "@/components/app/stage-workbench";

/**
 * Strategy agent — PRD §23.4 (Track 3 only).
 * Owner: Architect. Sequences the path to the target design Design decided —
 * it plans the order, it does not choose the destination.
 */
export default function StrategyPage() {
  return (
    <StageWorkbench
      phase="strategy"
      agent="strategy"
      title="Strategy"
      runLabel="Run Strategy agent"
      emptyTitle="No migration plan yet"
      emptyDescription="Sequences the path to the target architecture: orders modules by risk (lowest first), defines testable equivalence criteria per module for Testing to inherit, and flags undocumented business rules as open questions rather than guessing. Produces the versioned execution plan Development works through."
    />
  );
}
