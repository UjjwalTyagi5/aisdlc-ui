"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { cn } from "@/lib/utils";
import { prettySegment } from "@/lib/nav";
import { getProject } from "@/lib/api/projects";
import { getWorkspace } from "@/lib/api/workspaces";
import { getUserDetail } from "@/lib/api/users";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";

/**
 * Reserved child segments of /projects — anything else in that position is a
 * project id.
 *
 * This replaces a UUID test that never matched: seeded project ids are slugs
 * (`payments-api`, `mobile-onboarding`), so the project crumb silently fell back
 * to `prettySegment("payments-api")` → "Payments Api" and the resolved name was
 * never used. Breadcrumbs are the primary "where am I" affordance for a scoped
 * viewer, so the crumb has to say the project's actual name.
 */
const RESERVED_PROJECT_SEGMENTS = new Set(["new"]);

/**
 * Derives a breadcrumb from the current pathname using `segmentLabels`
 * in `lib/nav.ts`. A project-id segment (/projects/<uuid>) resolves to the
 * project's display name via react-query (cache-shared with the project page);
 * other dynamic segments fall back to the raw value.
 *
 * Elevation (Plan 06): trail items use --font-mono + muted-foreground for
 * the "control room" crumb style from northstar.html (.crumbs). The current
 * page segment is slightly brighter (foreground) so it reads as the focus.
 * Component API is unchanged.
 */
export function Breadcrumbs() {
  const pathname = usePathname();

  // /projects/<uuid>/... — resolve the project's name for its crumb.
  const parts = pathname.split("/").filter(Boolean);
  const projectId =
    parts[0] === "projects" && parts[1] && !RESERVED_PROJECT_SEGMENTS.has(parts[1])
      ? parts[1]
      : null;
  const projectQ = useQuery({
    queryKey: qk.projects.detail((projectId ?? "") as ProjectId),
    queryFn: () => getProject(projectId as ProjectId),
    enabled: projectId !== null,
    staleTime: 5 * 60_000,
  });

  // /users/<ssoSubject> — resolve the person's display name for its crumb
  // (an ssoSubject like "pending|name@co.com" is unreadable raw, and isn't a
  // UUID so it wouldn't match the project case above).
  const userSeg = parts[0] === "users" && parts[1] ? parts[1] : null;
  const userId = userSeg ? decodeURIComponent(userSeg) : null;
  const userQ = useQuery({
    queryKey: qk.users.detail(userId ?? ""),
    queryFn: () => getUserDetail(userId as string),
    enabled: userId !== null,
    staleTime: 5 * 60_000,
  });

  // The Business Unit a project belongs to, so a project trail reads as the
  // PRD's actual hierarchy (Business Unit → Project → stage, §12) instead of
  // flattening two scopes into one crumb. Only fetched on project routes.
  const parentUnitId = projectQ.data?.workspaceId ?? null;
  const unitQ = useQuery({
    queryKey: qk.workspaces.detail(parentUnitId ?? ""),
    queryFn: () => getWorkspace(parentUnitId as string),
    enabled: parentUnitId !== null,
    staleTime: 5 * 60_000,
  });

  const segments = React.useMemo(() => {
    const raw = pathname
      .split("/")
      .filter(Boolean)
      .map((seg, idx, arr) => ({
        label:
          seg === projectId
            ? (projectQ.data?.name ?? prettySegment(seg))
            : seg === userSeg
              ? (userQ.data?.displayName ?? prettySegment(seg))
              : prettySegment(seg),
        href: "/" + arr.slice(0, idx + 1).join("/"),
        isLast: idx === arr.length - 1,
      }));

    // Splice the owning Business Unit in ahead of the project crumb. Inserted
    // rather than derived from the path because the URL has no unit segment —
    // /projects/<id> deliberately doesn't nest under /workspaces/<id>.
    if (projectId && unitQ.data) {
      const at = raw.findIndex((s) => s.href === `/projects/${projectId}`);
      if (at > 0) {
        raw.splice(at, 0, {
          label: unitQ.data.displayName,
          href: `/workspaces/${unitQ.data.id}`,
          isLast: false,
        });
      }
    }
    return raw;
  }, [
    pathname,
    projectId,
    projectQ.data?.name,
    userSeg,
    userQ.data?.displayName,
    unitQ.data,
  ]);

  if (segments.length === 0) return null;

  return (
    <Breadcrumb>
      <BreadcrumbList className="font-mono text-xs tracking-wide">
        {segments.map((s) => (
          <React.Fragment key={s.href}>
            <BreadcrumbItem>
              {s.isLast ? (
                <BreadcrumbPage className="text-foreground font-medium">
                  {s.label}
                </BreadcrumbPage>
              ) : (
                <BreadcrumbLink asChild>
                  <Link
                    href={s.href}
                    className={cn(
                      "text-muted-foreground transition-colors hover:text-foreground",
                    )}
                  >
                    {s.label}
                  </Link>
                </BreadcrumbLink>
              )}
            </BreadcrumbItem>
            {!s.isLast && (
              <BreadcrumbSeparator className="text-muted-foreground/40" />
            )}
          </React.Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  );
}
