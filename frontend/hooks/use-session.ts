"use client";

import { useRawSession } from "@/components/auth/session-provider";
import type { Session } from "@/lib/auth/types";

/**
 * Returns the current session.
 *
 * Overloads:
 *   `useSession()`              → Session | null  (caller must handle null)
 *   `useSession({ required })`  → Session         (throws if missing)
 *
 * Inside the `(app)` route group middleware guarantees a session exists,
 * so prefer `useSession({ required: true })` there to drop the null-guard.
 */
export function useSession(opts: { required: true }): Session;
export function useSession(opts?: { required?: false }): Session | null;
export function useSession(opts?: { required?: boolean }): Session | null {
  const s = useRawSession();
  if (opts?.required && !s) {
    throw new Error(
      "useSession({ required: true }) called without a session. " +
        "Render this component inside the (app) route group, or make the call optional.",
    );
  }
  return s;
}
