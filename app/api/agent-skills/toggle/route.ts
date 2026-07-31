import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { SkillToggleResult } from "@/lib/schemas/agent-skills";

/** Enable/disable a vendor or custom skill. */
export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/agent-skills/toggle", {
    method: "POST",
    body,
    schema: SkillToggleResult,
  });
}
