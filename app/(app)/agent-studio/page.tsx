import type { Metadata } from "next";

import { AgentStudio } from "@/components/agent-studio/agent-studio";

export const metadata: Metadata = {
  title: "Agent Studio",
};

export default function AgentStudioPage() {
  return <AgentStudio />;
}
