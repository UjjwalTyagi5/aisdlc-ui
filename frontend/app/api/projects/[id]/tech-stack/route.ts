import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ProjectTechStack } from "@/lib/schemas/tech-stacks";

type Ctx = { params: Promise<{ id: string }> };

/** The project's tech-stack options, its selection, and the stack its agents follow. */
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/tech-stack`, { schema: ProjectTechStack });
}

/** Pick the project's stack (body: {tech_stack_id}, null = follow the Business Unit default). */
export async function PUT(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/tech-stack`, {
    method: "PUT",
    body: await req.json(),
    schema: ProjectTechStack,
  });
}
