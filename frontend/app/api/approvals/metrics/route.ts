import { buildQueueMetrics } from "@/lib/mock/approval-fixtures";

// DUMMY-DATA SEAM: swap to bffProxy("/approvals/metrics") when backend exists.
export function GET() {
  return Response.json(buildQueueMetrics());
}
