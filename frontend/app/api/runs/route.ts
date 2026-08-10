import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { RunCreateResponse } from "@/lib/schemas";

export function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(search ? `/runs?${search}` : "/runs");
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  return bffProxy("/runs", { method: "POST", body, schema: RunCreateResponse });
}
