"use client";

import * as React from "react";
import Link from "next/link";
import { Building2, FolderKanban, KeyRound, Lock, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { useAccessScope } from "@/hooks/use-access-scope";

/**
 * Empty states for scoped data — the other half of filtering.
 *
 * Once lists are filtered, "empty" becomes ambiguous in a way it never was
 * before: it can mean the scope genuinely has nothing, that the viewer belongs
 * to no scope at all, or that they followed a link to something outside their
 * boundary. All three previously rendered as the same bare "No projects yet",
 * which reads as a broken page to the one person who most needs a clear answer.
 * Each gets its own state and its own next step.
 */

/**
 * The viewer belongs to no Business Unit or project yet — the honest terminal
 * state for a person who has been created but not assigned. Distinct from a
 * failed request, which must never render here (see `useAccessScope`'s three
 * states) because "we couldn't load it" would read as "you're not allowed".
 */
export function NoScopeAccess({
  resource = "data",
  className,
}: {
  resource?: string;
  className?: string;
}) {
  const { role } = useAccessScope();
  return (
    <EmptyState
      icon={Lock}
      title={`No ${resource} in your scope yet`}
      description={
        <>
          You aren&apos;t assigned to any {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} or projects, so
          there is nothing here to show
          {role ? ` for your ${ROLE_META[role].label} role` : ""}. Ask your{" "}
          {BUSINESS_UNIT_LABEL} Admin to add you to a project — access is granted per scope, not
          globally.
        </>
      }
      // The one place a confused viewer can see their own bindings and
      // permissions without an admin's help — worth a link from every state
      // that exists because of a boundary.
      action={
        <Button asChild variant="outline">
          <Link href="/my-access">
            <KeyRound className="size-4" aria-hidden />
            Review my access
          </Link>
        </Button>
      }
      className={className}
    />
  );
}

/**
 * The viewer's scope is real but currently holds nothing of this kind. Names the
 * scope explicitly so the count on screen is attributable — "no approvals in
 * Payments" is actionable where a bare "all caught up" leaves them wondering
 * whether the filter ate something.
 */
export function EmptyInScope({
  resource,
  scopeName,
  description,
  action,
  className,
}: {
  resource: string;
  scopeName?: string | null;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <EmptyState
      icon={FolderKanban}
      title={scopeName ? `No ${resource} in ${scopeName}` : `No ${resource} yet`}
      description={
        description ??
        `Nothing of this kind exists in your scope right now. Anything added${
          scopeName ? ` to ${scopeName}` : ""
        } will appear here.`
      }
      action={action}
      className={className}
    />
  );
}

/**
 * The viewer followed a link to something outside their boundary.
 *
 * Deliberately says "not available to you" rather than naming what was
 * requested: repeating the id or name back would confirm it exists, which is the
 * cross-scope fact being withheld. The API answers 404 for the same reason — see
 * app/api/projects/[id]/route.ts.
 */
export function OutOfScope({
  kind = "project",
  backHref,
  backLabel,
  className,
}: {
  kind?: "project" | "business unit" | "resource";
  backHref?: string;
  backLabel?: string;
  className?: string;
}) {
  const href = backHref ?? (kind === "business unit" ? "/workspaces" : "/projects");
  const label =
    backLabel ??
    (kind === "business unit" ? `Your ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}` : "Your projects");

  return (
    <div className="mx-auto max-w-2xl p-4 md:p-8">
      <EmptyState
        icon={ShieldAlert}
        title={`This ${kind} isn't available to you`}
        description={
          <>
            It either doesn&apos;t exist or sits outside the {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}{" "}
            and projects you&apos;re assigned to. Access is granted per scope — if you need it, ask
            the {kind === "business unit" ? "Organization" : BUSINESS_UNIT_LABEL} Admin who owns it.
          </>
        }
        action={
          <Button asChild variant="outline">
            <Link href={href}>
              {kind === "business unit" ? (
                <Building2 className="size-4" aria-hidden />
              ) : (
                <FolderKanban className="size-4" aria-hidden />
              )}
              {label}
            </Link>
          </Button>
        }
        secondaryAction={
          <Button asChild variant="ghost">
            <Link href="/my-access">
              <KeyRound className="size-4" aria-hidden />
              What can I access?
            </Link>
          </Button>
        }
        className={className}
      />
    </div>
  );
}

/**
 * Wraps a scoped list in the right one of the three states above.
 *
 * `isEmpty` is the caller's own "did the filtered list come back empty" — this
 * component only decides WHICH empty message that deserves, which depends on
 * scope facts the list itself doesn't have.
 */
export function ScopedListBoundary({
  isEmpty,
  resource,
  scopeName,
  emptyDescription,
  emptyAction,
  children,
}: {
  isEmpty: boolean;
  resource: string;
  scopeName?: string | null;
  emptyDescription?: React.ReactNode;
  emptyAction?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { scope, isOrgWide, businessUnitIds, projectIds } = useAccessScope();

  if (!isEmpty) return <>{children}</>;

  // Resolved, not org-wide, and bound to nothing at all → the person has no
  // assignment yet, which is a different problem from an empty scope.
  const unbound =
    scope !== null && !isOrgWide && businessUnitIds.length === 0 && projectIds.length === 0;
  if (unbound) return <NoScopeAccess resource={resource} />;

  return (
    <EmptyInScope
      resource={resource}
      scopeName={scopeName}
      description={emptyDescription}
      action={emptyAction}
    />
  );
}
