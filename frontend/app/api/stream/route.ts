/**
 * The org-wide live-update stream (SSE).
 *
 * OPEN AND QUIET. It holds the connection and heartbeats, and sends no events.
 *
 * It used to synthesise them: a `cost.update` every few seconds naming a random
 * fixture run, and artifact-status changes picked out of the fixture array. On a
 * database with no runs and no artifacts, that made the most convincing lie on
 * the platform — numbers that MOVED, which is what a live feed is trusted for.
 *
 * The endpoint stays rather than being deleted, because a page opening an
 * EventSource against a 404 retries in a loop. An open stream with nothing on it
 * is the accurate report: nothing is happening.
 *
 * BACKLOG: a FastAPI event source (run progress, cost ticks, artifact
 * transitions) for this route to relay. `app/api/runs/[id]/stream` already
 * relays a real per-run stream — this is its org-wide counterpart.
 */
export const dynamic = "force-dynamic";

export function GET() {
  const encoder = new TextEncoder();
  let heartbeat: ReturnType<typeof setInterval> | undefined;

  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(": stream opened\n\n"));
      heartbeat = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(": heartbeat\n\n"));
        } catch {
          // Client went away between the cancel callback and this tick.
          if (heartbeat) clearInterval(heartbeat);
        }
      }, 15_000);
    },
    cancel() {
      if (heartbeat) clearInterval(heartbeat);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
