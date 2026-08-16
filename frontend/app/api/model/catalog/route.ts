import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { CatalogProvider } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/catalog", { schema: z.array(CatalogProvider) });
}
