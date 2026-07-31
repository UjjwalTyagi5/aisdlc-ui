"use client";

import { StageWorkbench } from "@/components/app/stage-workbench";

/**
 * Validation agent — PRD §24.6 (Track 4 only).
 * Owner: QA / Tester. Running the parallel validation is itself Consequential
 * because it drives live runs; accepting the result as cutover-ready is the
 * Sign-off.
 */
export default function ValidationPage() {
  return (
    <StageWorkbench
      phase="validation"
      agent="validation"
      title="Validation"
      runLabel="Run Validation agent"
      emptyTitle="No validation report yet"
      emptyDescription="Runs the parallel-parity principle for each migration flavour: original bot versus migrated bot, original bot versus new code, or a smoke-test and drift check for infrastructure. Every discrepancy is classified as a defect or an intentional change — nothing is left ambiguous before cutover."
    />
  );
}
