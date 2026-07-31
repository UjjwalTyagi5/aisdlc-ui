import type { Meta, StoryObj } from "@storybook/react";

import {
  InlineCommentThread,
  type InlineComment,
} from "@/components/app/inline-comment-thread";
import type { UserRef } from "@/lib/schemas";

const meta: Meta<typeof InlineCommentThread> = {
  title: "App/InlineCommentThread",
  component: InlineCommentThread,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof InlineCommentThread>;

const ada: UserRef = {
  id: "u_admin" as UserRef["id"],
  name: "Ada Lovelace",
  email: "ada@acme.test",
  initials: "AL",
};
const grace: UserRef = {
  id: "u_member" as UserRef["id"],
  name: "Grace Hopper",
  email: "grace@acme.test",
  initials: "GH",
};

const now = Date.now();

const comments: InlineComment[] = [
  {
    id: "c1",
    author: "agent",
    body: "I noticed the new schema adds a NOT NULL column without a default — back-fill strategy?",
    createdAt: new Date(now - 1000 * 60 * 40).toISOString(),
    severity: "blocking",
  },
  {
    id: "c2",
    author: grace,
    body: "Good catch. Back-fill happens via migration 0043 — linked in the ADR.",
    createdAt: new Date(now - 1000 * 60 * 30).toISOString(),
  },
  {
    id: "c3",
    author: ada,
    body: "Merging once CI is green. Nice.",
    createdAt: new Date(now - 1000 * 60 * 10).toISOString(),
    severity: "nit",
    resolved: true,
  },
];

export const Populated: Story = {
  args: {
    comments,
    currentUser: ada,
    onReply: async () => {},
    onResolve: async () => {},
  },
};

export const Empty: Story = {
  args: {
    comments: [],
    currentUser: ada,
    onReply: async () => {},
  },
};

export const ReadOnly: Story = {
  args: {
    comments,
    currentUser: ada,
  },
};
