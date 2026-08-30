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
  action,
}: {
  title?: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-4xl p-4 md:p-8">
      {/* Synthesized as a `forbidden`-coded ApiError, not passed via `description`
       *  alone. ApiErrorState's Lock-icon/no-retry "forbidden" treatment — the
       *  one this component's own doc comment above already promised — is keyed
       *  off `error?.code === "forbidden"`, not off the mere absence of an
       *  `error` prop. Passing only `title`/`description` (the previous code)
       *  left `forbidden` permanently false here, so this component silently
       *  rendered the generic destructive/AlertTriangle treatment instead of
       *  the calmer amber Lock one, AND would have made the new `action` prop
       *  below — gated on `forbidden` in ApiErrorState — dead code that could
       *  never render. Wrapping `description` as the ApiError's `message`
       *  fixes both: same visible text, but now via the actually-forbidden
       *  branch. */}
      <ApiErrorState
        title={title}
        error={{ code: "forbidden", message: description }}
        action={action}
      />
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
