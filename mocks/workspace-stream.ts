import { HttpResponse, type HttpResponseResolver } from "msw";

import type {
  ArtifactId,
  ProjectId,
  RunId,
  StreamEvent,
} from "@/lib/schemas";

import { ARTIFACTS, RUNS } from "./fixtures";

/**
 * Workspace-wide event stream. Sends:
 *   - an initial `connected` comment so EventSource flips to OPEN fast,
 *   - periodic `cost.update` events so the live pill has something to
 *     display while demoing,
 *   - an `artifact.updated` ping once per minute against a random artifact
 *     so cache-invalidation wiring is visibly exercised.
 *
 * Real implementation will fan-out Temporal signal events here.
 */
export const workspaceStreamHandler: HttpResponseResolver = () => {
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
      // Opening comment — some proxies buffer until the first line.
      controller.enqueue(encoder.encode(": stream opened\n\n"));

      const send = (ev: StreamEvent) => {
        if (closed) return;
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`));
      };

      // Heartbeat every 15s so intermediaries don't half-close.
      const heartbeat = setInterval(() => {
        if (closed) return;
        controller.enqueue(encoder.encode(": heartbeat\n\n"));
      }, 15_000);

      // Fake cost ticks every ~8s against a random run.
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

      // One artifact update per minute so query invalidation + notification
      // badge is visibly exercised.
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

      (controller as ReadableStreamDefaultController & { _cleanup?: () => void })._cleanup =
        () => {
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

  return new HttpResponse(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
};
