import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelAllowEntry } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/allowed/bu?${qs}`, { schema: z.array(ModelAllowEntry) });
}

export async function PUT(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  const body: unknown = await req.json();
  return bffProxy(`/model/allowed/bu?${qs}`, { method: "PUT", body, schema: z.array(ModelAllowEntry) });
}
