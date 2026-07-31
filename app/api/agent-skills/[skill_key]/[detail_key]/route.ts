import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { SkillDetail } from "@/lib/schemas/agent-skills";

/**
 * Full skill detail (incl. markdown body) at `/agent-skills/{origin}/{skill_key}`.
 *
 * NOTE: the first path segment here is the `origin` (vendor|custom) and the
 * second is the skill key. The level-1 folder is named `[skill_key]` only
 * because Next.js requires sibling dynamic segments to share one slug name (the
 * PUT/DELETE route at this level is keyed by skill_key). We read the params
 * back into their true roles below.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ skill_key: string; detail_key: string }> },
) {
  const { skill_key: origin, detail_key: skillKey } = await params;
  const search = req.nextUrl.searchParams.toString();
  const base = `/agent-skills/${encodeURIComponent(origin)}/${encodeURIComponent(skillKey)}`;
  return bffProxy(search ? `${base}?${search}` : base, {
    schema: SkillDetail,
  });
}
