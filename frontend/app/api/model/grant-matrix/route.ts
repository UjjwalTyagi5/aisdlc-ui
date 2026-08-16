import { bffProxy } from "@/lib/bff/proxy";
import { ModelGrantMatrix } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/grant-matrix", { schema: ModelGrantMatrix });
}
