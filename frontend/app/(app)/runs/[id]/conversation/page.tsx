"use client";

import { useParams } from "next/navigation";

import { RunConversation } from "@/components/runs/run-conversation";

export default function RunConversationPage() {
  const { id } = useParams<{ id: string }>();
  return <RunConversation runId={id} />;
}
