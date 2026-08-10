"use client";

import * as React from "react";

/**
 * Client-only MSW bootstrap.
 *  - Runs only in development.
 *  - Flag `NEXT_PUBLIC_API_MOCKS=off` disables mocks for backend wiring tests.
 *  - Service-worker file must exist at /mockServiceWorker.js — run `pnpm msw:init` once.
 *
 * Blocks first paint until the worker is ready so initial queries hit the mock, not the real network.
 */
/**
 * The in-flight (or settled) `worker.start()`, held at module scope.
 *
 * `worker` is a singleton, and starting it twice throws "cannot configure an
 * already enabled network". React 18's StrictMode deliberately mounts effects
 * twice in development, so the naive version — start inside the effect, guard
 * only the `setReady` with a cancelled flag — called `start()` on both passes
 * and threw on the second. The cancelled flag never helped: it prevented a
 * state update, not the second start.
 *
 * Caching the promise makes the call idempotent, and a repeat mount awaits the
 * same start rather than racing a new one.
 */
let startPromise: Promise<void> | null = null;

function startWorkerOnce(): Promise<void> {
  startPromise ??= (async () => {
    const { worker } = await import("@/mocks/browser");
    try {
      await worker.start({
        onUnhandledRequest: "bypass",
        serviceWorker: { url: "/mockServiceWorker.js" },
      });
    } catch (e) {
      // Fast Refresh can replace this module while the worker from the previous
      // evaluation is still enabled, which resets `startPromise` but not the
      // worker. An already-enabled network is the state we wanted anyway, so
      // treat it as success instead of blanking the app.
      if (e instanceof Error && /already enabled/i.test(e.message)) return;
      throw e;
    }
  })();
  return startPromise;
}

export function MswInit({ children }: { children: React.ReactNode }) {
  const enabled =
    process.env.NODE_ENV === "development" &&
    process.env.NEXT_PUBLIC_API_MOCKS !== "off";

  const [ready, setReady] = React.useState(!enabled);

  React.useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    startWorkerOnce()
      .catch((e) => {
        console.error("[msw] failed to start worker", e);
      })
      // Render either way: a failed worker should surface as failing requests
      // you can debug, not as a permanently blank screen.
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  if (!ready) return null;
  return <>{children}</>;
}
