import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { OrgModelGrant } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/allowed/org", { schema: z.array(OrgModelGrant) });
}

export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/allowed/org", { method: "PUT", body, schema: z.array(OrgModelGrant) });
}
