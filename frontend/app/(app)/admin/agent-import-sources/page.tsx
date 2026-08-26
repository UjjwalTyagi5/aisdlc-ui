import type { Metadata } from "next";

import { ImportSourceAllowlist } from "@/components/agent-studio/import-source-allowlist";

export const metadata: Metadata = {
  title: "Import sources",
};

export default function AgentImportSourcesPage() {
  return <ImportSourceAllowlist />;
}
