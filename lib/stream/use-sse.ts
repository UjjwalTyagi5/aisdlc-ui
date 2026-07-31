"use client";

import * as React from "react";

export type SseState = "idle" | "connecting" | "connected" | "reconnecting" | "offline";

export interface UseSseOptions<T> {
  /** SSE URL, or `null` to disable. */
  url: string | null;
  /** Called for every parsed event. */
  onEvent: (event: T) => void;
  /** Parse raw `data` — defaults to `JSON.parse`. */
  parse?: (raw: string) => T;
  /** Max reconnect attempts before giving up. 0 = infinite. */
  maxAttempts?: number;
  /** Report connection state transitions upward. */
  onState?: (state: SseState) => void;
}

/**
 * Reconnecting SSE subscriber. EventSource handles auto-reconnect on network
 * blips, but we wrap it so we can:
 *   - surface a typed `SseState` to the UI (pill in the top bar)
 *   - back off exponentially with a cap
 *   - stop reconnecting once a max-attempt budget is hit
 *
 * Pair with `parse`-generic `T` to decode Zod-validated events at the edge.
 */
export function useSse<T>({
  url,
  onEvent,
  parse = (raw) => JSON.parse(raw) as T,
  maxAttempts = 0,
  onState,
}: UseSseOptions<T>) {
  // Stable refs for the latest callbacks so the effect can depend only on `url`.
  const onEventRef = React.useRef(onEvent);
  const parseRef = React.useRef(parse);
  const onStateRef = React.useRef(onState);
  React.useEffect(() => {
    onEventRef.current = onEvent;
    parseRef.current = parse;
    onStateRef.current = onState;
  }, [onEvent, parse, onState]);

  const [state, setState] = React.useState<SseState>("idle");

  React.useEffect(() => {
    if (!url) {
      setState("idle");
      onStateRef.current?.("idle");
      return;
    }

    let cancelled = false;
    let attempts = 0;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const setStateBoth = (s: SseState) => {
      setState(s);
      onStateRef.current?.(s);
    };

    const connect = () => {
      if (cancelled) return;
      setStateBoth(attempts === 0 ? "connecting" : "reconnecting");
      try {
        es = new EventSource(url, { withCredentials: true });
      } catch {
        scheduleReconnect();
        return;
      }
      es.onopen = () => {
        attempts = 0;
        setStateBoth("connected");
      };
      es.onmessage = (msg) => {
        try {
          const parsed = parseRef.current(msg.data);
          onEventRef.current(parsed);
        } catch (err) {
          // Parse failure shouldn't kill the stream — log and continue
          console.warn("[sse] parse failed", err);
        }
      };
      es.onerror = () => {
        // EventSource auto-reconnects but we want visible state + backoff cap
        es?.close();
        es = null;
        if (maxAttempts > 0 && attempts >= maxAttempts) {
          setStateBoth("offline");
          return;
        }
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      attempts += 1;
      // 500ms, 1s, 2s, 4s, 8s, … capped at 30s.
      const delay = Math.min(30_000, 500 * 2 ** (attempts - 1));
      setStateBoth("reconnecting");
      timer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      es?.close();
    };
  }, [url, maxAttempts]);

  return state;
}
