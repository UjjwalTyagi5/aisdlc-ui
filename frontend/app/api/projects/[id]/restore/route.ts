import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Bring an archived project back — proxied to FastAPI
 * `POST /projects/{id}/restore`.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/restore`, { method: "POST" });
}
