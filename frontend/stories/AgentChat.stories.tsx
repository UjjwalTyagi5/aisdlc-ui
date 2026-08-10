import type { Meta, StoryObj } from "@storybook/react";
import * as React from "react";

import {
  AgentChatDrawer,
  type AgentChatMessage,
} from "@/components/app/agent-chat-drawer";
import { Button } from "@/components/ui/button";

const meta: Meta<typeof AgentChatDrawer> = {
  title: "App/AgentChatDrawer",
  component: AgentChatDrawer,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof AgentChatDrawer>;

function Demo({ seed, streaming }: { seed: AgentChatMessage[]; streaming?: boolean }) {
  const [open, setOpen] = React.useState(true);
  const [messages, setMessages] = React.useState<AgentChatMessage[]>(seed);
  const [busy, setBusy] = React.useState(false);

  const send = async (text: string) => {
    setMessages((m) => [
      ...m,
      {
        id: `u${Date.now()}`,
        role: "user",
        content: text,
        createdAt: new Date().toISOString(),
      },
    ]);
    if (!streaming) return;
    setBusy(true);
    const id = `a${Date.now()}`;
    setMessages((m) => [
      ...m,
      {
        id,
        role: "agent",
        content: "",
        createdAt: new Date().toISOString(),
        streaming: true,
      },
    ]);
    const reply = "Thinking about it… here's a first pass that mirrors your selected story's acceptance criteria.";
    for (let i = 1; i <= reply.length; i++) {
      await new Promise((r) => setTimeout(r, 20));
      setMessages((m) =>
        m.map((msg) => (msg.id === id ? { ...msg, content: reply.slice(0, i) } : msg)),
      );
    }
    setMessages((m) => m.map((msg) => (msg.id === id ? { ...msg, streaming: false } : msg)));
    setBusy(false);
  };

  return (
    <>
      <Button onClick={() => setOpen(true)}>Open chat</Button>
      <AgentChatDrawer
        open={open}
        onOpenChange={setOpen}
        context={{ page: "Requirements", artifactTitle: "Replay failed ingest batch" }}
        messages={messages}
        onSend={send}
        busy={busy}
      />
    </>
  );
}

export const Empty: Story = {
  render: () => <Demo seed={[]} />,
};

export const Populated: Story = {
  render: () => (
    <Demo
      seed={[
        {
          id: "m1",
          role: "user",
          content: "Summarize the blocking comments on this PR.",
          createdAt: new Date(Date.now() - 1000 * 60 * 5).toISOString(),
        },
        {
          id: "m2",
          role: "agent",
          content:
            "Two blockers:\n• Missing Idempotency-Key on the replay endpoint\n• NOT NULL column has no back-fill plan",
          createdAt: new Date(Date.now() - 1000 * 60 * 4).toISOString(),
        },
      ]}
    />
  ),
};

export const StreamingReply: Story = {
  render: () => <Demo streaming seed={[]} />,
};
