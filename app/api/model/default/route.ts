import { type NextRequest } from "next/server";

import { setModelDefaultOffering } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: mutates the shared PROVIDERS array directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function PUT(req: NextRequest) {
  const body = (await req.json()) as { offering_id: string };
  setModelDefaultOffering(body.offering_id);
  return Response.json({ ok: true });
}
