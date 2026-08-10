import type { Metadata } from "next";

import { Landing } from "@/components/landing/landing";
import { getSession } from "@/lib/auth/session";

export const metadata: Metadata = {
  title: "SDLC Platform — Agentic software delivery, governed by PwC",
  description:
    "Coordinated AI agents across Requirements, Design, Development, Code Review, Security, Testing, Deployment, and Documentation — with human approval at every gate, tenant isolation, and end-to-end audit.",
};

export default async function RootPage() {
  const session = await getSession();
  const consoleHref = session?.permissions.includes("platform:*") ? "/platform" : "/dashboard";
  return <Landing authed={Boolean(session)} consoleHref={consoleHref} />;
}
