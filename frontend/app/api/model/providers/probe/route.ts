import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ProbeResult } from "@/lib/schemas/model";

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/providers/probe", { method: "POST", body, schema: ProbeResult });
}
