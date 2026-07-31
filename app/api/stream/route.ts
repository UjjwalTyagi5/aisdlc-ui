import type { ArtifactId, ProjectId, RunId, StreamEvent } from "@/lib/schemas";
import { ARTIFACTS, RUNS } from "@/mocks/fixtures";

// DUMMY-DATA SEAM: a synthetic workspace-wide SSE stream (cost ticks, artifact
// updates) — a nice-to-have live-update feed, but must exist as a plain Next
// route too so a page never 404s on it if MSW isn't intercepting (e.g. a
// stale/unregistered Service Worker). Mirrors mocks/workspace-stream.ts's
// synthesis exactly (duplicated rather than shared, since MSW's HttpResponse
// and a plain Response aren't interchangeable at the type level) — see
// [[msw-dual-runtime-mutation-rule]].
export function GET() {
  const encoder = new TextEncoder();

  const pickRun = (): RunId =>
    (RUNS[Math.floor(Math.random() * RUNS.length)]?.id ?? RUNS[0]!.id) as RunId;
  const pickArtifact = () => {
    const a = ARTIFACTS[Math.floor(Math.random() * ARTIFACTS.length)];
    return {
      id: (a?.id ?? ARTIFACTS[0]!.id) as ArtifactId,
      projectId: (a?.projectId ?? ARTIFACTS[0]!.projectId) as ProjectId,
      status: a?.status ?? "approved",
    };
  };

  let closed = false;

  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(": stream opened\n\n"));

      const send = (ev: StreamEvent) => {
        if (closed) return;
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));
      };

      const heartbeat = setInterval(() => {
        if (closed) return;
        controller.enqueue(encoder.encode(": heartbeat\n\n"));
      }, 15_000);

      const cost = setInterval(() => {
        const runId = pickRun();
        const inputTokens = 1200 + Math.floor(Math.random() * 3000);
        const outputTokens = 200 + Math.floor(Math.random() * 800);
        send({
          type: "cost.update",
          runId,
          cost: {
            usd: Number((inputTokens * 3e-6 + outputTokens * 1.5e-5).toFixed(4)),
            inputTokens,
            outputTokens,
          },
          at: new Date().toISOString(),
        });
      }, 8_000);

      const artifact = setInterval(() => {
        const { id, status } = pickArtifact();
        send({
          type: "artifact.updated",
          runId: pickRun(),
          artifactId: id,
          status,
          at: new Date().toISOString(),
        });
        if (Math.random() < 0.25) {
          send({
            type: "hitl.pending",
            runId: pickRun(),
            artifactId: id,
            title: "Gate waiting on your approval",
            at: new Date().toISOString(),
          });
        }
      }, 60_000);

      (controller as ReadableStreamDefaultController & { _cleanup?: () => void })._cleanup = () => {
        closed = true;
        clearInterval(heartbeat);
        clearInterval(cost);
        clearInterval(artifact);
      };
    },
    cancel() {
      closed = true;
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
