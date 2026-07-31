"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Building2, Globe, Plug } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listConnectors } from "@/lib/api/connectors";
import { getProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { PHASE_LABEL } from "@/lib/agents";
import type { Phase } from "@/lib/schemas/enums";
import type { ProjectId } from "@/lib/schemas";

/**
 * The connectors this project runs on, as a contributor sees them (PRD §34.3).
 *
 * Read-only by design and for everyone, including the Project Admin: choosing
 * is done once, on Settings → Tools per stage, and duplicating the control
 * here would give two places to disagree. A Developer, BA, Architect, Tester
 * or Security Engineer needs to know which integrations their agents can
 * reach — they never need to change it.
 *
 * Each row names the level the connector was onboarded at, because "why can't
 * I see Slack here" is answered by the level, not by this project: an org-wide
 * connector is available everywhere, a {BUSINESS_UNIT_LABEL}-scoped one only
 * inside its own unit.
 */
export function ProjectConnectorsCard({ projectId }: { projectId: ProjectId }) {
  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });

  const workspaceId = projectQ.data?.workspaceId ?? null;
  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(workspaceId),
    queryFn: () => listConnectors(workspaceId),
    enabled: projectQ.isSuccess,
  });

  // project.connectors maps a stage to the connector kinds enabled on it.
  // Invert it: a contributor thinks "can I reach Jira", not "what is wired to
  // the design stage", so the connector is the row and the stages are detail.
  const perStage = projectQ.data?.connectors ?? {};
  const stagesByKind = new Map<string, Phase[]>();
  for (const [stage, kinds] of Object.entries(perStage)) {
    for (const kind of kinds ?? []) {
      const list = stagesByKind.get(kind) ?? [];
      list.push(stage as Phase);
      stagesByKind.set(kind, list);
    }
  }

  const inherited = (connectorsQ.data ?? []).filter((c) => c.installed);
  const selected = inherited.filter((c) => stagesByKind.has(c.kind));
  const loading = projectQ.isLoading || connectorsQ.isLoading;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Plug className="size-4" aria-hidden />
          Connectors on this project
        </CardTitle>
        <CardDescription>
          Inherited from the organization and{" "}
          {projectQ.data?.workspaceId ? "this" : "no"} {BUSINESS_UNIT_LABEL.toLowerCase()}, then
          selected for this project by its Project Admin on{" "}
          <Link
            href={`/projects/${encodeURIComponent(projectId)}/settings`}
            className="text-brand-bright underline"
          >
            Settings → Tools per stage
          </Link>
          .
        </CardDescription>
      </CardHeader>

      <CardContent>
        {loading ? (
          <p className="text-muted-foreground text-sm">Loading…</p>
        ) : selected.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No connectors are enabled on this project yet.{" "}
            {inherited.length > 0
              ? `${inherited.length} ${inherited.length === 1 ? "is" : "are"} available to enable.`
              : "Nothing has been onboarded at the organization or " +
                `${BUSINESS_UNIT_LABEL.toLowerCase()} level yet.`}
          </p>
        ) : (
          <ul className="divide-y rounded-md border">
            {selected.map((c) => {
              const stages = stagesByKind.get(c.kind) ?? [];
              const orgWide = c.scope === "organization";
              return (
                <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5">
                  <span className="text-[13px] font-medium">{c.name}</span>
                  <Badge variant="outline" className="gap-1 font-mono text-[10px]">
                    {orgWide ? (
                      <Globe className="size-3" aria-hidden />
                    ) : (
                      <Building2 className="size-3" aria-hidden />
                    )}
                    {orgWide ? "org-wide" : BUSINESS_UNIT_LABEL.toLowerCase()}
                  </Badge>
                  <span className="text-muted-foreground ml-auto text-[11.5px]">
                    {stages.map((s) => PHASE_LABEL[s] ?? s).join(" · ")}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
