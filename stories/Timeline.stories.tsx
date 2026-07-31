import type { Meta, StoryObj } from "@storybook/react";

import { ActivityTimeline } from "@/components/app/activity-timeline";
import type { RunId, Step, StepId } from "@/lib/schemas";

const meta: Meta<typeof ActivityTimeline> = {
  title: "App/ActivityTimeline",
  component: ActivityTimeline,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof ActivityTimeline>;

const runId = "run_2141" as RunId;
const at = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const steps: Step[] = [
  {
    id: "s1" as StepId,
    runId,
    index: 0,
    kind: "plan",
    agent: "requirements",
    title: "Build run plan",
    status: "approved",
    startedAt: at(30),
    completedAt: at(29),
    durationMs: 60_000,
    summary: "5 steps, est. cost $0.12",
    cost: { usd: 0.04, inputTokens: 6_200, outputTokens: 900 },
    model: { provider: "anthropic", id: "claude-sonnet-4-6" },
  },
  {
    id: "s2" as StepId,
    runId,
    index: 1,
    kind: "tool_call",
    agent: "requirements",
    title: "Fetch Jira issue ING-214",
    status: "approved",
    startedAt: at(29),
    completedAt: at(28),
    durationMs: 60_000,
    summary: "Pulled issue + 3 comments + 1 linked confluence page",
  },
  {
    id: "s3" as StepId,
    runId,
    index: 2,
    kind: "llm_call",
    agent: "requirements",
    title: "Draft stories + AC",
    status: "approved",
    startedAt: at(27),
    completedAt: at(26),
    durationMs: 60_000,
    cost: { usd: 0.08, inputTokens: 18_400, outputTokens: 2_800 },
    model: { provider: "anthropic", id: "claude-sonnet-4-6", promptVersion: "v3" },
  },
  {
    id: "s4" as StepId,
    runId,
    index: 3,
    kind: "guardrail_check",
    agent: "requirements",
    title: "PII scan",
    status: "approved",
    startedAt: at(25),
    completedAt: at(25),
    durationMs: 2_000,
    summary: "Clean",
  },
  {
    id: "s5" as StepId,
    runId,
    index: 4,
    kind: "artifact_write",
    agent: "requirements",
    title: "Write Jira sub-tasks (dry run)",
    status: "failed",
    startedAt: at(24),
    completedAt: at(23),
    durationMs: 60_000,
    error: { code: "rate_limited", message: "Jira returned 429 after 3 retries" },
  },
  {
    id: "s6" as StepId,
    runId,
    index: 5,
    kind: "hitl_wait",
    agent: "requirements",
    title: "Await PM approval",
    status: "awaiting_approval",
    startedAt: at(22),
    completedAt: null,
    durationMs: null,
    summary: "Pending since 2m ago",
  },
];

export const Flat: Story = { args: { steps } };
export const GroupedByRun: Story = { args: { steps, groupBy: "run" } };
export const Empty: Story = { args: { steps: [] } };
export const Interactive: Story = { args: { steps, onStepSelect: () => {} } };
