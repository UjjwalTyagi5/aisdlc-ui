import { type NextRequest } from "next/server";

import { createConversation, listConversations } from "@/lib/mock/conversation-fixtures";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly. When the
// backend conversations service lands, replace both bodies with bffProxy(...).

/** List the caller's sessions for an agent+project (query: agent_id, project_id). */
export function GET(req: NextRequest) {
  const agentId = req.nextUrl.searchParams.get("agent_id") ?? "";
  const projectId = req.nextUrl.searchParams.get("project_id") ?? "";
  return Response.json(listConversations(agentId, projectId));
}

/** Create a new chat session. */
export async function POST(req: NextRequest) {
  const body = (await req.json()) as { agent_id: string; project_id: string; title?: string };
  const session = createConversation({
    agentId: body.agent_id,
    projectId: body.project_id,
    title: body.title,
  });
  return Response.json(session, { status: 201 });
}
