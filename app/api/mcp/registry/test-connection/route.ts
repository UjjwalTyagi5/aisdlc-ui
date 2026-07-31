import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { McpTestResult } from "@/lib/schemas/mcp";

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/mcp/registry/test-connection", {
    method: "POST",
    body,
    schema: McpTestResult,
  });
}
