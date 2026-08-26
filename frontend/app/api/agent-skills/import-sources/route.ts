import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ImportSourceEntry, ImportSourceList } from "@/lib/schemas/agent-skills";

/** The org's approved external import sources — readable by anyone. */
export function GET() {
  return bffProxy("/agent-skills/import-sources", { schema: ImportSourceList });
}

/** Add an entry to the allowlist (Org Admin only — bffProxy normalizes a bare
 *  403 to a clear "you don't have access" state). */
export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/agent-skills/import-sources", {
    method: "POST",
    body,
    schema: ImportSourceEntry.pick({ id: true, source_pattern: true, label: true }),
  });
}
