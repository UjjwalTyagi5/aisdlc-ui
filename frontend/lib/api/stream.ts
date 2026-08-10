import { StreamEvent, type RunId } from "@/lib/schemas";

import { API_BASE } from "./client";

export interface SubscribeOptions {
  onEvent: (event: StreamEvent) => void;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/**
 * Subscribe to a run's server-sent event stream.
 * Returns a disposer that closes the connection and cancels reconnection.
 *
 * Chunk 15 builds a React hook (`useRunStream`) on top of this.
 */
export function subscribeToRun(runId: RunId, opts: SubscribeOptions): () => void {
  const source = new EventSource(`${API_BASE}/runs/${encodeURIComponent(runId)}/stream`, {
    withCredentials: true,
  });

  source.onopen = () => opts.onOpen?.();
  source.onerror = (e) => opts.onError?.(e);
  source.onmessage = (msg) => {
    try {
      const parsed = StreamEvent.safeParse(JSON.parse(msg.data));
      if (!parsed.success) {
        console.warn("[stream] unrecognized event", msg.data);
        return;
      }
      opts.onEvent(parsed.data);
    } catch (err) {
      opts.onError?.(err);
    }
  };

  return () => {
    source.close();
    opts.onClose?.();
  };
}
