"use client";

import * as React from "react";
import Link from "next/link";
import { Archive, ArchiveRestore, Clock, Layers, MoreVertical, Settings2, XCircle } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PhasePipeline } from "@/components/app/phase-pipeline";
import { DeliveryStatusBadge } from "@/components/app/delivery-status-badge";
import { RequireRole } from "@/components/auth/require-role";
import type { Project } from "@/lib/schemas";
import { TRACK_META } from "@/lib/tracks";

// Exported so the table renders the SAME template chip, track badge, approval
// pill, owner stack and action menu as the card. Two copies of a badge is how one
// of them quietly stops matching the other.
export const TEMPLATE_LABEL: Record<Project["template"], string> = {
  web_app: "Web app",
  microservice: "Microservice",
  data_pipeline: "Data pipeline",
  blank: "Blank",
};

/** Mono badge tones map to the northstar .tmpl classes */
export const TEMPLATE_TONE: Record<Project["template"], string> = {
  web_app: "border-info/30 bg-info/10 text-info",
  microservice: "border-primary/30 bg-primary/10 text-brand-bright",
  data_pipeline: "border-success/30 bg-success/10 text-success",
  blank: "border-border bg-muted text-muted-foreground",
};

/** Delivery track (PRD §6) — which agent roster this project runs, distinct from `template`. */
export function TrackBadge({ track, className }: { track: Project["track"]; className?: string }) {
  const meta = TRACK_META[track];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full border border-line-soft bg-surface-2 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground",
            className,
          )}
        >
          <Layers className="size-2.5" aria-hidden />
          Track {meta.number} · {meta.shortLabel}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[260px]">
        <p className="font-medium">{meta.label}</p>
        <p className="text-muted-foreground mt-1 text-[12px]">{meta.summary}</p>
      </TooltipContent>
    </Tooltip>
  );
}

/** A project a Project Admin created is invisible-to-pipeline until its BU
 *  Admin approves it (governance approval, not an agent gate) — surface that
 *  state everywhere the project appears rather than hiding the card. */
export function ApprovalStatusBadge({ status }: { status: Project["approvalStatus"] }) {
  if (status === "pending_approval") {
    return (
      <span className="border-warning/30 bg-warning/10 text-warning inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider">
        <Clock className="size-2.5" aria-hidden />
        Pending BU approval
      </span>
    );
  }
  if (status === "rejected") {
    return (
      <span className="border-destructive/30 bg-destructive/10 text-destructive inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider">
        <XCircle className="size-2.5" aria-hidden />
        Rejected
      </span>
    );
  }
  return null;
}

export interface ProjectCardProps {
  project: Project;
  onArchive?: (project: Project) => void;
  onRestore?: (project: Project) => void;
  /** "grid" mimics a dashboard card; "row" flattens for the list view. */
  variant?: "grid" | "row";
  className?: string;
  style?: React.CSSProperties;
}

export function ProjectCard({
  project,
  onArchive,
  onRestore,
  variant = "grid",
  className,
  style,
}: ProjectCardProps) {
  const href = `/projects/${project.id}`;
  const actions = (
    <ProjectActions project={project} onArchive={onArchive} onRestore={onRestore} />
  );

  if (variant === "row") {
    return (
      <li
        className={cn(
          "group flex items-center gap-3 rounded-xl border border-line-soft bg-panel-elevated px-4 py-3 transition-all duration-200",
          "hover:-translate-y-px hover:border-border hover:shadow-[0_4px_12px_-4px_oklch(0_0_0_/_0.4)]",
          project.archived && "opacity-60",
          className,
        )}
      >
        <Link
          href={href}
          className="flex min-w-0 flex-1 items-center gap-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
        >
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="flex items-center gap-2">
              <span className="font-display truncate text-sm font-bold tracking-tight">
                {project.name}
              </span>
              <TrackBadge track={project.track} />
              <span
                className={cn(
                  "shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider",
                  TEMPLATE_TONE[project.template],
                )}
              >
                {TEMPLATE_LABEL[project.template]}
              </span>
              <ApprovalStatusBadge status={project.approvalStatus} />
              <DeliveryStatusBadge status={project.deliveryStatus} />
              {project.archived && (
                <Badge variant="outline" className="text-muted-foreground shrink-0">
                  archived
                </Badge>
              )}
            </div>
            <span className="text-muted-foreground truncate font-mono text-[11px]">
              {project.slug} · updated {formatDistanceToNow(new Date(project.lastActivityAt), { addSuffix: true })}
            </span>
          </div>
          <div className="hidden shrink-0 sm:block">
            <PhasePipeline pipeline={project.pipeline} density="compact" />
          </div>
          <CreatedByLabel owners={project.owners} className="hidden shrink-0 md:flex" />
        </Link>
        {actions}
      </li>
    );
  }

  return (
    <div
      className={cn(
        "group relative flex flex-col overflow-hidden rounded-[var(--radius-xl)] border border-line-soft bg-panel-elevated",
        "shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.5)]",
        "transition-all duration-200",
        "hover:-translate-y-[3px] hover:border-border hover:shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_16px_32px_-12px_oklch(0_0_0_/_0.65)]",
        project.archived && "opacity-60",
        className,
      )}
      style={{
        animationName: "rise",
        animationDuration: "0.6s",
        animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
        animationFillMode: "both",
        ...style,
      }}
    >
      {/* Brand accent line — hidden until hover */}
      <div
        className="absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-brand-gradient-from to-brand-gradient-to opacity-0 transition-opacity duration-200 group-hover:opacity-100"
        aria-hidden
      />

      <Link
        href={href}
        className="flex flex-1 flex-col gap-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-panel-elevated rounded-[var(--radius-xl)]"
      >
        {/* Card top */}
        <div className="flex flex-col gap-2 p-[18px] pb-4">
          <div>
            <h3 className="font-display truncate text-[17px] font-bold leading-tight tracking-[-0.02em] text-foreground">
              {project.name}
            </h3>
            <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              {project.slug}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <TrackBadge track={project.track} />
            <span
              className={cn(
                "shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider",
                TEMPLATE_TONE[project.template],
              )}
            >
              {TEMPLATE_LABEL[project.template]}
            </span>
            <ApprovalStatusBadge status={project.approvalStatus} />
            <DeliveryStatusBadge status={project.deliveryStatus} />
          </div>

          <p className="line-clamp-2 min-h-[2.5rem] text-[12.5px] leading-[1.5] text-muted-foreground">
            {project.description ?? <span className="italic">No description</span>}
          </p>
        </div>

        {/* Mini-pipeline — real pipeline[] from §9 contract (ED-5: empty = idle) */}
        <div className="px-[18px]">
          <PhasePipeline pipeline={project.pipeline} density="compact" />
        </div>

        {/* Card footer */}
        <div className="mt-4 flex items-center justify-between gap-2 border-t border-line-soft px-[18px] py-3">
          <CreatedByLabel owners={project.owners} />
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
            {formatDistanceToNow(new Date(project.lastActivityAt), { addSuffix: true })}
          </span>
        </div>
      </Link>

      <div className="flex items-center justify-end gap-1 border-t border-line-soft px-3 py-2">
        {project.archived && (
          <span className="text-muted-foreground mr-auto font-mono text-[10px] uppercase tracking-wider">
            archived
          </span>
        )}
        {actions}
      </div>
    </div>
  );
}

function OwnerStack({ owners }: { owners: Project["owners"] }) {
  return (
    <div
      className="flex -space-x-2"
      aria-label={`${owners.length} owner${owners.length > 1 ? "s" : ""}`}
    >
      {owners.slice(0, 3).map((o) => (
        <Avatar key={o.id} className="border-panel-elevated size-6 border-2">
          {o.avatarUrl && <AvatarImage src={o.avatarUrl} alt="" />}
          <AvatarFallback className="text-[10px]">{o.initials}</AvatarFallback>
        </Avatar>
      ))}
      {owners.length > 3 && (
        <Avatar className="border-panel-elevated size-6 border-2">
          <AvatarFallback className="text-[10px]">+{owners.length - 3}</AvatarFallback>
        </Avatar>
      )}
    </div>
  );
}

/** The project's creator (owners[0] — set once at creation, see
 *  createProjectRecord in lib/mock/project-fixtures.ts) — an avatar stack
 *  alone doesn't answer "who made this", so name it explicitly. */
export function CreatedByLabel({ owners, className }: { owners: Project["owners"]; className?: string }) {
  const creator = owners[0];
  if (!creator) return null;
  return (
    <div className={cn("flex min-w-0 items-center gap-1.5", className)}>
      <OwnerStack owners={owners} />
      <span className="text-muted-foreground truncate font-mono text-[10.5px]">
        Created by <span className="text-foreground">{creator.name}</span>
      </span>
    </div>
  );
}

export function ProjectActions({
  project,
  onArchive,
  onRestore,
}: {
  project: Project;
  onArchive?: (p: Project) => void;
  onRestore?: (p: Project) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-7" aria-label="Project actions">
          <MoreVertical className="size-4" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem asChild>
          <Link href={`/projects/${project.id}`}>
            <Settings2 className="size-4" aria-hidden />
            Open project
          </Link>
        </DropdownMenuItem>
        <RequireRole capability="project:update">
          {project.archived ? (
            <DropdownMenuItem onSelect={() => onRestore?.(project)}>
              <ArchiveRestore className="size-4" aria-hidden />
              Restore
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onSelect={() => onArchive?.(project)}>
              <Archive className="size-4" aria-hidden />
              Archive
            </DropdownMenuItem>
          )}
        </RequireRole>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
