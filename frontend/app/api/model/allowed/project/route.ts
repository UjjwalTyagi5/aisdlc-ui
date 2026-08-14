import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ProjectModelSelection } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/allowed/project?${qs}`, { schema: ProjectModelSelection });
}

export async function PUT(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  const body: unknown = await req.json();
  return bffProxy(`/model/allowed/project?${qs}`, { method: "PUT", body, schema: ProjectModelSelection });
}
