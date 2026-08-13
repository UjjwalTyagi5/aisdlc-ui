import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Per-project adjustments to what a role may do with an agent — proxied to FastAPI
 * `GET/PUT/DELETE /projects/{id}/agent-access-overrides`.
 *
 * EMPTY IS STILL THE NORMAL ANSWER, and now it means something: this project uses the
 * built-in matrix. Before, it meant the table did not exist.
 *
 * The stored value is the FULL involvement level rather than a delta, so "what does a
 * QA reach on this project" is one lookup instead of a base plus a patch. Deleting the
 * row IS the reset — there is no separate "clear" state to get out of step with it.
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/agent-access-overrides`);
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/projects/${encodeURIComponent(id)}/agent-access-overrides`, {
    method: "PUT",
    body,
  });
}

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(
    `/projects/${encodeURIComponent(id)}/agent-access-overrides${qs ? `?${qs}` : ""}`,
    { method: "DELETE" },
  );
}
