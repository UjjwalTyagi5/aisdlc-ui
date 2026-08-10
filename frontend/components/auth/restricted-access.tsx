"use client";

import * as React from "react";

import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";

/**
 * Full-page denied state. Use at the top of a page component when the user
 * lacks the permission the page requires. Defense-in-depth only — the backend
 * require_permission dependency is the authoritative boundary.
 *
 * Renders ApiErrorState with a Lock icon treatment (no retry CTA) via the
 * title + description props. Matches the existing cost/page.tsx gate pattern.
 */
export function RestrictedAccess({
  title = "Access restricted",
  description,
}: {
  title?: string;
  description: string;
}) {
  return (
    <div className="mx-auto max-w-4xl p-4 md:p-8">
      <ApiErrorState title={title} description={description} />
    </div>
  );
}

/**
 * Inline gate for individual elements. Renders `children` only when the
 * session carries `permission` (admin:* wildcard passes); renders `fallback`
 * otherwise. Uses useSession() with no args → Session | null (fail-closed:
 * a missing session renders the fallback, never the protected content).
 */
export function RequirePermission({
  permission,
  children,
  fallback = null,
}: {
  permission: string;
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const session = useSession();
  return hasPermission(session, permission) ? <>{children}</> : <>{fallback}</>;
}
