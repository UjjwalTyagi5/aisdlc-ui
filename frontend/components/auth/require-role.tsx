"use client";

import * as React from "react";

import { useRawSession } from "@/components/auth/session-provider";
import { can } from "@/lib/auth/capabilities";
import type { Capability, Role } from "@/lib/auth/types";

interface BaseProps {
  children: React.ReactNode;
  /** Rendered when the check fails. Default: nothing. */
  fallback?: React.ReactNode;
}

type Props = BaseProps & ({ role: Role } | { capability: Capability });

/**
 *  <RequireRole role="admin">       <DeleteButton /> </RequireRole>
 *  <RequireRole capability="audit:view"> <AuditLink /> </RequireRole>
 *
 * Silently renders the fallback (or nothing) when the user lacks the
 * permission. Use for pure UI gating — always enforce on the server too.
 */
export function RequireRole({ children, fallback = null, ...perm }: Props) {
  const s = useRawSession();
  if (!s) return fallback;

  const allowed =
    "role" in perm
      ? roleGte(s.role, perm.role)
      : can(s.role, perm.capability);

  return allowed ? <>{children}</> : fallback;
}

const ORDER: Record<Role, number> = { viewer: 0, member: 1, admin: 2 };

function roleGte(current: Role, required: Role): boolean {
  return ORDER[current] >= ORDER[required];
}
