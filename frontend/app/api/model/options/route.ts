import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelOptions } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/options${qs ? `?${qs}` : ""}`, { schema: ModelOptions });
}
