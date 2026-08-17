import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Agent chat sessions — proxied to FastAPI `GET/POST /conversations`, the
 * backend conversations service the seam comment here was waiting for.
 *
 * Ownership is the backend's: it lists the CALLER's sessions (`created_by`) and
 * 403s a transcript belonging to someone else. The fixture store had no notion of
 * an owner at all, so every session in it was everyone's.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const agentId = req.nextUrl.searchParams.get("agent_id") ?? "";
  const projectId = req.nextUrl.searchParams.get("project_id");
  const qs = new URLSearchParams({ agent_id: agentId });
  if (projectId) qs.set("project_id", projectId);
  return bffProxy(`/conversations?${qs.toString()}`);
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/conversations", { method: "POST", body });
}
