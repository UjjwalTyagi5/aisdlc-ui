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
export function MswInit({ children }: { children: React.ReactNode }) {
  const enabled =
    process.env.NODE_ENV === "development" &&
    process.env.NEXT_PUBLIC_API_MOCKS !== "off";

  const [ready, setReady] = React.useState(!enabled);

  React.useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    (async () => {
      const { worker } = await import("@/mocks/browser");
      await worker.start({
        onUnhandledRequest: "bypass",
        serviceWorker: { url: "/mockServiceWorker.js" },
      });
      if (!cancelled) setReady(true);
    })().catch((e) => {
      console.error("[msw] failed to start worker", e);
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  if (!ready) return null;
  return <>{children}</>;
}
