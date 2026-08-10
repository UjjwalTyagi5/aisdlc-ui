import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { SkillVersionList } from "@/lib/schemas/agent-skills";

/** Version history for a custom skill (query: agent_id, scope, scope_id). */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ skill_key: string }> },
) {
  const { skill_key: skillKey } = await params;
  const search = req.nextUrl.searchParams.toString();
  const base = `/agent-skills/${encodeURIComponent(skillKey)}/versions`;
  return bffProxy(search ? `${base}?${search}` : base, {
    schema: SkillVersionList,
  });
}
