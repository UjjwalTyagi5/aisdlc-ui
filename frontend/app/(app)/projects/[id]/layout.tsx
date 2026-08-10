"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Archive, Layers } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ProjectTabs } from "@/components/app/project-tabs";
import { getProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { TRACK_META } from "@/lib/tracks";
import { agentCountForTrack } from "@/lib/tracks";
import type { ProjectId } from "@/lib/schemas";

/**
 * Project shell — PRD §32.1, "Inside a Project": opening a project brings its
 * work together in one place.
 *
 * Owns the project's identity (name, delivery track, description) and the
 * in-project navigation, so every sub-page inherits the same context strip
 * instead of re-declaring it.
 */
export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });

  const project = projectQ.data;
  const track = project ? TRACK_META[project.track] : null;

  return (
    <div className="w-full">
      {/* ── Project context strip ──────────────────────────────────────── */}
      <div className="px-4 pt-4 md:px-10 md:pt-8">
        {projectQ.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-7 w-64" />
            <Skeleton className="h-4 w-96" />
          </div>
        ) : project ? (
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-2xl font-semibold tracking-tight">
                {project.name}
              </h1>

              {track && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge
                      variant="secondary"
                      className="gap-1 font-mono text-[10.5px] font-normal"
                    >
                      <Layers className="size-3" aria-hidden />
                      Track {track.number} · {track.shortLabel}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-[280px]">
                    <p className="font-medium">{track.label}</p>
                    <p className="text-muted-foreground mt-1 text-[12px]">
                      {track.summary}
                    </p>
                    <p className="text-muted-foreground mt-1 font-mono text-[11px]">
                      {agentCountForTrack(project.track)} agents · PRD {track.prdSection}
                    </p>
                  </TooltipContent>
                </Tooltip>
              )}

              {project.archived && (
                <Badge variant="outline" className="text-muted-foreground gap-1">
                  <Archive className="size-3" aria-hidden />
                  archived
                </Badge>
              )}
            </div>

            {project.description && (
              <p className="text-muted-foreground max-w-2xl text-sm">
                {project.description}
              </p>
            )}

            {project.owners[0] && (
              <p className="text-muted-foreground font-mono text-[11px]">
                Created by <span className="text-foreground">{project.owners[0].name}</span>
              </p>
            )}
          </div>
        ) : null}
      </div>

      {/* ── In-project nav ─────────────────────────────────────────────── */}
      <div className="mt-4 px-4 md:px-10">
        <ProjectTabs projectId={id} />
      </div>

      {children}
    </div>
  );
}
