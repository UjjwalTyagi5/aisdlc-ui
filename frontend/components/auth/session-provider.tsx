"use client";

import * as React from "react";

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
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** @internal — prefer `useSession` / `useCan`. */
export function useRawSession(): Session | null {
  return React.useContext(SessionContext);
}
