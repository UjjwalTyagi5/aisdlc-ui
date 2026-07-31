import { type NextRequest } from "next/server";

// DUMMY-DATA SEAM: acknowledges a decision/answer without persisting. The real
// endpoint sends a Temporal signal (hitl.decision / within_agent_clarification)
// via the existing signals route and is permission-gated server-side.
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return Response.json({ ok: true, id });
}
