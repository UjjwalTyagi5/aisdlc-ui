import { type NextRequest } from "next/server";

import { deleteConversation, renameConversation } from "@/lib/mock/conversation-fixtures";

// DUMMY-DATA SEAM: reads/writes the in-memory fixture store directly. When the
// backend conversations service lands, replace both bodies with bffProxy(...).

/** Rename an owned session. */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = (await req.json()) as { title: string };
  const session = renameConversation(id, body.title);
  if (!session) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(session);
}

/** Soft-delete an owned session. */
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  deleteConversation(id);
  return new Response(null, { status: 204 });
}
