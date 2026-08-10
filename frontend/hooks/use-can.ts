"use client";

import { useRawSession } from "@/components/auth/session-provider";
import { can } from "@/lib/auth/capabilities";
import type { Capability } from "@/lib/auth/types";

/**
 * Client-side capability check. Drives conditional rendering and
 * disabled-state of actions. Server enforcement is separate.
 *
 * Returns `false` when no session is present.
 */
export function useCan(capability: Capability): boolean {
  const s = useRawSession();
  if (!s) return false;
  return can(s.role, capability);
}
