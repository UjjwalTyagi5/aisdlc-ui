import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelProviderGrant } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/providers/grants", { schema: z.array(ModelProviderGrant) });
}

export async function PUT(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  const body: unknown = await req.json();
  return bffProxy(`/model/providers/grants?${qs}`, {
    method: "PUT",
    body,
    schema: z.array(ModelProviderGrant),
  });
}
