import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/default", { method: "PUT", body });
}
