"use client";

import { StageWorkbench } from "@/components/app/stage-workbench";

/**
 * Project Manager agent — sits between Design and Development.
 *
 * Owner: Scrum Master. Accepting a plan is a Sign-off, because it commits people and
 * dates rather than producing a document.
 *
 * Uses StageWorkbench rather than a bespoke page. The Requirements and Design pages are
 * hand-built because each has a viewer the other does not need — a story editor, a
 * Mermaid renderer. A plan has neither yet, and a fourth copy of the same artifact
 * list, chat drawer and activity dock would be four places to fix the next bug in.
 */
export default function PlanPage() {
  return (
    <StageWorkbench
      phase="plan"
      agent="plan"
      title="Plan"
      runLabel="Run Project Manager agent"
      emptyTitle="No plan yet"
      emptyDescription="Turns the accepted design into a work breakdown with estimates, traced back to the requirements that motivated each task. Reads the board's sprints, and its team capacity where the board exposes it — Azure DevOps does; Jira has no capacity API. Scheduling into sprints and assigning people are not built yet."
    />
  );
}
