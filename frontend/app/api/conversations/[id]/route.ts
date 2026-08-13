import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Rename or delete a chat session — proxied to FastAPI
 * `PATCH/DELETE /conversations/{id}`.
 */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/conversations/${encodeURIComponent(id)}`, { method: "PATCH", body });
}

export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}
