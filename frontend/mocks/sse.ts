import { HttpResponse, type HttpResponseResolver } from "msw";

import type { ArtifactId, RunId, StepId, StreamEvent } from "@/lib/schemas";

/**
 * Fake SSE source for a run. Emits a plausible lifecycle:
 *   run.started → step.progress × N → artifact.updated → hitl.pending
 * with cost.update events sprinkled in. Stops itself after ~20s.
 */
export const sseHandler: HttpResponseResolver = ({ params }) => {
  const runId = params.id as RunId;
  const encoder = new TextEncoder();

  const script: Array<{ delay: number; event: StreamEvent }> = [];
  const now = () => new Date().toISOString();

  script.push({
    delay: 0,
    event: { type: "run.started", runId, at: now() },
  });

  const steps = [
    "Plan run",
    "Fetch context",
    "Draft output",
    "Guardrail check",
    "Persist artifact",
  ] as const;
  let cumulativeInput = 0;
  let cumulativeOutput = 0;
  steps.forEach((title, i) => {
    const stepId = `step_live_${i + 1}` as StepId;
    script.push({
      delay: 600 + i * 400,
      event: {
        type: "step.progress",
        runId,
        stepId,
        title,
        status: "running",
        progress: 0.1,
        at: now(),
      },
    });
    // A few streaming deltas
    for (let d = 0; d < 4; d++) {
      script.push({
        delay: 700 + i * 400 + d * 150,
        event: {
          type: "step.output.delta",
          runId,
          stepId,
          delta: `Chunk ${d + 1} of step ${i + 1}. `,
          at: now(),
        },
      });
    }
    cumulativeInput += 3200 + i * 400;
    cumulativeOutput += 820 + i * 120;
    script.push({
      delay: 1500 + i * 400,
      event: {
        type: "cost.update",
        runId,
        cost: {
          usd: Number((cumulativeInput * 3e-6 + cumulativeOutput * 1.5e-5).toFixed(4)),
          inputTokens: cumulativeInput,
          outputTokens: cumulativeOutput,
        },
        at: now(),
      },
    });
    script.push({
      delay: 1600 + i * 400,
      event: {
        type: "step.progress",
        runId,
        stepId,
        title,
        status: "approved",
        progress: 1,
        at: now(),
      },
    });
  });

  const liveArtifactId = "art_live_1" as ArtifactId;
  script.push({
    delay: 4500,
    event: {
      type: "artifact.updated",
      runId,
      artifactId: liveArtifactId,
      status: "awaiting_approval",
      at: now(),
    },
  });
  script.push({
    delay: 4700,
    event: {
      type: "hitl.pending",
      runId,
      artifactId: liveArtifactId,
      title: "Requirements draft awaits approval",
      at: now(),
    },
  });

  const stream = new ReadableStream({
    async start(controller) {
      for (const { delay, event } of script) {
        await new Promise((r) => setTimeout(r, delay));
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      // Keep the connection open briefly so the client sees the tail
      await new Promise((r) => setTimeout(r, 2000));
      controller.close();
    },
  });

  return new HttpResponse(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
};
