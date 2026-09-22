import { bffProxy } from "@/lib/bff/proxy";
import { TechStackCatalog } from "@/lib/schemas/tech-stacks";

/** Tech-stack categories and the suggestions the editor offers for each. */
export function GET() {
  return bffProxy("/tech-stacks/catalog", { schema: TechStackCatalog });
}
