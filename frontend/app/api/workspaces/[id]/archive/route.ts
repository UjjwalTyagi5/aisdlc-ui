import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Archive a Business Unit — proxied to FastAPI
 * `POST /workspaces/{id}/archive`, gated there on `workspace:manage`.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/workspaces/${encodeURIComponent(id)}/archive`, { method: "POST" });
}
