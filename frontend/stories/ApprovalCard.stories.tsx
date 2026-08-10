import type { Meta, StoryObj } from "@storybook/react";

import { ApprovalCard } from "@/components/app/approval-card";
import type { UserRef } from "@/lib/schemas";

const meta: Meta<typeof ApprovalCard> = {
  title: "App/ApprovalCard",
  component: ApprovalCard,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof ApprovalCard>;

const ada: UserRef = {
  id: "u_admin" as UserRef["id"],
  name: "Ada Lovelace",
  email: "ada@acme.test",
  initials: "AL",
};

export const Pending: Story = {
  args: {
    status: "awaiting_approval",
    title: "Requirements draft",
    description: "4 stories with Given/When/Then acceptance criteria. Ready for PM review.",
    onApprove: () => {},
    onEdit: () => {},
    onReject: () => {},
    onRetry: () => {},
    onAskAgent: () => {},
  },
};

export const Submitting: Story = {
  args: {
    ...Pending.args,
    pending: true,
    pendingDecision: "approve",
  },
};

export const ApprovedTerminal: Story = {
  args: {
    status: "approved",
    title: "Requirements draft",
    decidedBy: ada,
    decidedAt: new Date(Date.now() - 1000 * 60 * 12).toISOString(),
  },
};

export const RejectedTerminal: Story = {
  args: {
    status: "rejected",
    title: "Design — API contract",
    decidedBy: ada,
    decidedAt: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
    reason: "The webhook endpoint must be idempotent — add an Idempotency-Key header parameter.",
  },
};

export const FailedTerminal: Story = {
  args: {
    status: "failed",
    title: "Code review",
    description: "Run aborted after 3 retries — escalated to a human.",
  },
};

export const ApproveOnly: Story = {
  args: {
    status: "awaiting_approval",
    title: "Tests generated",
    description: "18 new test cases, +12% coverage delta.",
    onApprove: () => {},
  },
};
