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
import { providerLabel } from "@/lib/models/provider-labels";
import { connectorKindLabel } from "@/lib/connectors";
import { listIntegrationAccess } from "@/lib/api/integration-access";
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
 * URL segments that group routes without being a page.
 *
 * `/admin` has no `page.tsx` — it only exists so `/admin/models`, `/admin/roles`
 * and the rest share a prefix. It was still producing a crumb, which was wrong
 * twice over: the link 404s, and "Administration" names a section the sidebar
 * does not have (Model Management lives under GOVERN). Dropping it makes the
 * trail agree with the navigation the reader can actually see.
 *
 * Add a segment here only when it has no page of its own — a crumb that omits
 * a real ancestor is a worse bug than one that includes a routing artefact.
 */
const NON_PAGE_SEGMENTS = new Set(["admin"]);

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

  // /admin/models/<provider> — a routing slug, not a word. `prettySegment`
  // title-cases it into "Openai" and "Bedrock" beside a heading that reads
  // "OpenAI" and "AWS Bedrock", so the crumb and the page disagree about the
  // name of the thing you are looking at. Same table the heading uses.
  const providerSeg =
    parts[0] === "admin" && parts[1] === "models" && parts[2] ? parts[2] : null;

  /**
   * /integrations/<id> — the same trap the provider slug had. `mcp_filesystem`
   * title-cases into "Mcp_filesystem" beside a heading reading "Filesystem",
   * and `jira` into "Jira" beside "Jira Cloud — Acme". The access list already
   * names every integration and the page has it cached, so the crumb resolves
   * from the same source the heading does rather than guessing from the URL.
   */
  const integrationSeg =
    parts[0] === "integrations" && parts[1] && parts[1] !== "callback" ? parts[1] : null;
  const integrationsQ = useQuery({
    queryKey: qk.integrationAccess.list(),
    queryFn: () => listIntegrationAccess(),
    enabled: integrationSeg !== null,
    staleTime: 5 * 60_000,
  });
  const integrationName = React.useMemo(() => {
    if (!integrationSeg) return null;
    const kind = integrationSeg.startsWith("mcp_") ? "mcp" : "connector";
    const row = (integrationsQ.data ?? []).find((r) => r.kind === kind && r.id === integrationSeg);
    // Falls back to the connector catalogue label, so a kind nobody has been
    // granted still reads as "Azure DevOps" rather than "Azure_devops".
    return row?.name ?? (kind === "connector" ? connectorKindLabel(integrationSeg) : null);
  }, [integrationSeg, integrationsQ.data]);

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
      // Filtered AFTER href construction below would be wrong — a dropped
      // ancestor must not shift the paths its descendants are built from. So
      // the map runs over the full path and the prefix is removed at the end.
      .map((seg, idx, arr) => ({
        label:
          seg === projectId
            ? (projectQ.data?.name ?? prettySegment(seg))
            : seg === userSeg
              ? (userQ.data?.displayName ?? prettySegment(seg))
              : seg === providerSeg
                ? providerLabel(decodeURIComponent(seg))
                : seg === integrationSeg && integrationName
                  ? integrationName
                  : prettySegment(seg),
        href: "/" + arr.slice(0, idx + 1).join("/"),
        isLast: idx === arr.length - 1,
        seg,
      }))
      .filter((s) => !NON_PAGE_SEGMENTS.has(s.seg));

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
          seg: "workspaces",
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
    providerSeg,
    integrationSeg,
    integrationName,
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
