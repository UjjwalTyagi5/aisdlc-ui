import type { Meta, StoryObj } from "@storybook/react";

import { StatusBadge, type StatusKey } from "@/components/ui/status-badge";
import { CostBadge } from "@/components/ui/cost-badge";

const meta: Meta = { title: "App/Status & Cost", tags: ["autodocs"] };
export default meta;

const all: StatusKey[] = [
  "draft",
  "queued",
  "running",
  "awaiting_approval",
  "approved",
  "rejected",
  "failed",
  "merged",
  "paused",
];

export const AllStatuses: StoryObj = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {all.map((s) => (
        <StatusBadge key={s} status={s} />
      ))}
    </div>
  ),
};

export const IconOnly: StoryObj = {
  render: () => (
    <div className="flex items-center gap-2">
      {all.map((s) => (
        <StatusBadge key={s} status={s} iconOnly />
      ))}
    </div>
  ),
};

export const CostInline: StoryObj = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <CostBadge usd={0.004} tokens={820} />
      <CostBadge usd={0.12} tokens={18_420} />
      <CostBadge usd={1.03} tokens={83_100} inputTokens={72_400} outputTokens={10_700} />
      <CostBadge usd={12.47} tokens={1_240_000} />
    </div>
  ),
};

export const CostCard: StoryObj = {
  render: () => (
    <CostBadge
      density="card"
      usd={4.25}
      tokens={216_400}
      inputTokens={180_000}
      outputTokens={36_400}
    />
  ),
};
