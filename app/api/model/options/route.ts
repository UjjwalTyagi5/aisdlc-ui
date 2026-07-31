import { DEFAULT_MODEL_ID, DEFAULT_OFFERING_ID, MODEL_OPTIONS } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: returns fixtures directly. When the LiteLLM-backed catalog
// lands, replace the body with: return bffProxy("/model/options", { schema: ModelOptions }).
//
// force-dynamic: no request param/dynamic API used, so Next's Full Route
// Cache would otherwise statically cache the first response to disk — see
// the identical note on app/api/agent-profiles/summary/route.ts.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({
    options: MODEL_OPTIONS,
    default_offering_id: DEFAULT_OFFERING_ID,
    default_model_id: DEFAULT_MODEL_ID,
  });
}
