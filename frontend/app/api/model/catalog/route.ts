import { bffProxy } from "@/lib/bff/proxy";

/**
 * The provider catalogue — proxied to FastAPI `GET /model/catalog`.
 *
 * force-dynamic stays: with no request param and no dynamic API in the handler,
 * Next's Full Route Cache would otherwise persist the first response to disk and
 * keep serving it after the tenant's providers change.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/model/catalog");
}
