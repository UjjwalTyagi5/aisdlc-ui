import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelProvider } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/providers${qs ? `?${qs}` : ""}`, { schema: z.array(ModelProvider) });
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/providers", { method: "POST", body, schema: ModelProvider });
}
