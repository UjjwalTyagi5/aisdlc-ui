"use client";

/**
 * Projects as a table — the admin's view of a portfolio.
 *
 * WHY THIS EXISTS ALONGSIDE ProjectCard. A card is the right shape for a handful of
 * projects you know by name: it gives each one room and rewards recognition. An
 * Organization or Business Unit Admin is doing something else — scanning thirty of
 * them for the two that are stuck — and cards make that harder, because the same
 * field sits in a different place in every tile. A table puts one field in one column
 * so the eye runs down it.
 *
 * NOT A NEW VOCABULARY. Every badge here is imported from project-card.tsx rather
 * than rebuilt: the same template chip, track badge, approval pill, owner stack and
 * action menu. A table that invented its own would drift from the cards within a
 * release, and the two are shown to the same person a toggle apart.
 *
 * NO BUSINESS UNIT COLUMN. The page already groups by unit and prints its name as the
 * section heading, so a column would repeat the heading on every row.
 */
import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PhasePipeline } from "@/components/app/phase-pipeline";
import { DeliveryStatusBadge } from "@/components/app/delivery-status-badge";
import {
  ApprovalStatusBadge,
  CreatedByLabel,
  ProjectActions,
  TEMPLATE_LABEL,
  TEMPLATE_TONE,
  TrackBadge,
} from "@/components/app/project-card";
import type { Project } from "@/lib/schemas";

export interface ProjectsTableProps {
  projects: readonly Project[];
  onArchive?: (project: Project) => void;
  onRestore?: (project: Project) => void;
  className?: string;
}

export function ProjectsTable({
  projects,
  onArchive,
  onRestore,
  className,
}: ProjectsTableProps) {
  return (
    /* The wrapper scrolls, not the page. A wide table inside a page that scrolls
       horizontally takes the whole layout with it, including the sidebar. */
    <div
      className={cn(
        "border-line-soft bg-panel-elevated overflow-x-auto rounded-xl border",
        className,
      )}
    >
      <Table>
        <TableHeader>
          <TableRow className="border-line-soft hover:bg-transparent">
            <TableHead className="min-w-[220px]">Project</TableHead>
            <TableHead className="min-w-[160px]">Status</TableHead>
            {/* Progressive disclosure by width, in priority order: the pipeline is the
                widest thing here and the first to go, then the owners, then the
                classification. Name and status survive at every width because a row
                that cannot say which project it is has stopped being useful. */}
            <TableHead className="hidden min-w-[150px] sm:table-cell">Track</TableHead>
            <TableHead className="hidden min-w-[180px] lg:table-cell">Pipeline</TableHead>
            <TableHead className="hidden md:table-cell">Owners</TableHead>
            <TableHead className="min-w-[110px]">Updated</TableHead>
            <TableHead className="w-10">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {projects.map((project) => (
            <TableRow
              key={project.id}
              className={cn(
                "border-line-soft group",
                project.archived && "opacity-60",
              )}
            >
              <TableCell className="py-2.5">
                {/* The link wraps only the name, not the row. A row-wide click target
                    swallows the action menu's own clicks and gives a keyboard user one
                    stop for two destinations. */}
                <Link
                  href={`/projects/${project.id}`}
                  className="focus-visible:ring-ring flex flex-col gap-0.5 rounded-sm focus-visible:ring-2 focus-visible:outline-none"
                >
                  <span className="font-display flex items-center gap-2 text-[13px] font-bold tracking-tight">
                    <span className="truncate">{project.name}</span>
                    {project.archived && (
                      <Badge variant="outline" className="text-muted-foreground shrink-0">
                        archived
                      </Badge>
                    )}
                  </span>
                  <span className="text-muted-foreground truncate font-mono text-[11px]">
                    {project.slug}
                  </span>
                </Link>
              </TableCell>

              <TableCell className="py-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <DeliveryStatusBadge status={project.deliveryStatus} />
                  <ApprovalStatusBadge status={project.approvalStatus} />
                </div>
              </TableCell>

              <TableCell className="hidden py-2.5 sm:table-cell">
                <div className="flex flex-wrap items-center gap-1.5">
                  <TrackBadge track={project.track} />
                  <span
                    className={cn(
                      "shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wider uppercase",
                      TEMPLATE_TONE[project.template],
                    )}
                  >
                    {TEMPLATE_LABEL[project.template]}
                  </span>
                </div>
              </TableCell>

              <TableCell className="hidden py-2.5 lg:table-cell">
                <PhasePipeline pipeline={project.pipeline} density="compact" />
              </TableCell>

              <TableCell className="hidden py-2.5 md:table-cell">
                <CreatedByLabel owners={project.owners} />
              </TableCell>

              <TableCell className="text-muted-foreground py-2.5 font-mono text-[11px] whitespace-nowrap">
                {formatDistanceToNow(new Date(project.lastActivityAt), { addSuffix: true })}
              </TableCell>

              <TableCell className="py-2.5">
                <ProjectActions
                  project={project}
                  onArchive={onArchive}
                  onRestore={onRestore}
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
