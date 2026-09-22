import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { TechStack } from "@/lib/schemas/tech-stacks";

/** Make a Business Unit stack its default, or clear it (body: {is_default}). */
export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/tech-stacks/${encodeURIComponent(id)}/default`, {
    method: "PUT",
    body: await req.json(),
    schema: TechStack,
  });
}
