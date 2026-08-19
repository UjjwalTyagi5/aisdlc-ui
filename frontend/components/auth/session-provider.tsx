"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import type { Session } from "@/lib/auth/types";

const SessionContext = React.createContext<Session | null>(null);

export interface SessionProviderProps {
  value: Session | null;
  children: React.ReactNode;
}

/**
 * Receives the session fetched server-side via `getSession()` and exposes
 * it to client hooks (`useSession`, `useCan`). Stable across re-renders.
 */
export function SessionProvider({ value, children }: SessionProviderProps) {
  useTokenRefresh(value !== null);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/**
 * Pick up a role granted since this session's token was minted.
 *
 * Permissions and `platform_role` are baked into the token at login. Everything a
 * page FETCHES is resolved live from bindings, so somebody granted a role mid-session
 * sees the new project on their dashboard immediately — while the navigation and the
 * role chip, which read the token claim, still show them the old one. Live data next
 * to a stale identity reads as a broken product, and the person it happens to has no
 * way to know that signing out would fix it.
 *
 * ONCE PER MOUNT, NOT ON A TIMER. The window that matters is "an admin changed my
 * access while I was signed in", and a page load is when a person is already
 * expecting the app to catch up. Polling would spend a request a minute against a
 * thing that changes a handful of times in an account's life.
 *
 * `router.refresh()` only after something actually changed — the route handler
 * rewrites httpOnly cookies the server components read, so re-rendering is what makes
 * the new role visible, and doing it unconditionally would re-render every page load
 * for nothing.
 *
 * It cannot widen anybody: the backend re-resolves from the database, and a stale
 * token is under-privileged rather than over. Failure is silent by design — a refresh
 * that cannot run must never sign anybody out.
 */
function useTokenRefresh(signedIn: boolean) {
  const router = useRouter();
  const done = React.useRef(false);

  React.useEffect(() => {
    if (!signedIn || done.current) return;
    done.current = true;

    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/auth/refresh", { method: "POST" });
        // 204 is "nothing to do" — mock mode, signed out, or the backend declined.
        if (!cancelled && res.status === 200) router.refresh();
      } catch {
        // Silent: see above.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn, router]);
}

/** @internal — prefer `useSession` / `useCan`. */
export function useRawSession(): Session | null {
  return React.useContext(SessionContext);
}
