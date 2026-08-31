"use client";

import * as React from "react";
import { Filter, LayoutGrid, List, Search, Table2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";

/**
 * "table" is offered to an Organization or Business Unit Admin only, and is their
 * default — they scan a portfolio rather than recognise a handful of projects.
 * Everyone else keeps grid and list exactly as before. See `canUseTable`.
 */
export type ProjectsView = "grid" | "list" | "table";
export type ProjectsSort = "recent" | "name" | "template";
export type TemplateFilter =
  | "all"
  | "web_app"
  | "microservice"
  | "data_pipeline"
  | "blank";

/**
 * The view this viewer should see, from the URL and their standing.
 *
 * Pure and exported so the one case worth pinning has a test: an admin's bookmarked
 * `?view=table` opened by a contributor. Casting the param straight to the union —
 * which is what every other filter here does — would render a table with no toggle to
 * leave it by, because the Table button is not drawn for them.
 */
export function resolveProjectsView(
  requested: string | null,
  canUseTable: boolean,
): ProjectsView {
  if (requested === "grid" || requested === "list") return requested;
  if (requested === "table" && canUseTable) return "table";
  // An admin scans a portfolio and a contributor works on a few projects they know by
  // name, so the same absent param means different things to each.
  return canUseTable ? "table" : "grid";
}

export interface ProjectsToolbarState {
  search: string;
  template: TemplateFilter;
  sort: ProjectsSort;
  showArchived: boolean;
  view: ProjectsView;
}

export interface ProjectsToolbarProps {
  value: ProjectsToolbarState;
  onChange: (patch: Partial<ProjectsToolbarState>) => void;
  className?: string;
  /** Show the Table toggle. Admins only — see ProjectsView. */
  canUseTable?: boolean;
}

const TEMPLATE_OPTIONS: Array<{ value: TemplateFilter; label: string }> = [
  { value: "all", label: "All templates" },
  { value: "web_app", label: "Web app" },
  { value: "microservice", label: "Microservice" },
  { value: "data_pipeline", label: "Data pipeline" },
  { value: "blank", label: "Blank" },
];

export function ProjectsToolbar({
  value,
  onChange,
  className,
  canUseTable = false,
}: ProjectsToolbarProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 sm:flex-row sm:items-center",
        className,
      )}
      style={{
        animationName: "rise",
        animationDuration: "0.6s",
        animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
        animationFillMode: "both",
        animationDelay: "0.04s",
      }}
    >
      {/* Search — elevated with mono affordance */}
      <div className="relative flex-1">
        <Search
          className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          value={value.search}
          onChange={(e) => onChange({ search: e.target.value })}
          placeholder="Search projects…"
          className="h-9 border-line-soft bg-panel-elevated pl-9 font-mono text-sm placeholder:text-muted-foreground/60 focus-visible:border-primary"
          aria-label="Search projects"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {/* Filter icon + template select */}
        <div className="flex items-center gap-1.5 rounded-[9px] border border-line-soft bg-panel-elevated px-3 h-9">
          <Filter className="size-3.5 text-muted-foreground" aria-hidden />
          <Select
            value={value.template}
            onValueChange={(v) => onChange({ template: v as TemplateFilter })}
          >
            <SelectTrigger
              className="h-auto border-0 bg-transparent p-0 text-[12.5px] text-muted-foreground shadow-none focus:ring-0 focus-visible:ring-0 w-[110px]"
              aria-label="Template filter"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TEMPLATE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="font-mono text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Select
          value={value.sort}
          onValueChange={(v) => onChange({ sort: v as ProjectsSort })}
        >
          <SelectTrigger
            className="h-9 w-36 border-line-soft bg-panel-elevated text-[12.5px]"
            aria-label="Sort"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="recent" className="font-mono text-xs">Most recent</SelectItem>
            <SelectItem value="name" className="font-mono text-xs">Name A→Z</SelectItem>
            <SelectItem value="template" className="font-mono text-xs">Template</SelectItem>
          </SelectContent>
        </Select>

        {/* Archived toggle */}
        <div className="flex items-center gap-2 rounded-[9px] border border-line-soft bg-panel-elevated px-3 h-9">
          <Switch
            id="archived"
            checked={value.showArchived}
            onCheckedChange={(v) => onChange({ showArchived: v })}
          />
          <Label htmlFor="archived" className="cursor-pointer font-mono text-[11px] text-muted-foreground">
            Archived
          </Label>
        </div>

        {/* View toggle — grid / list, plus table for an admin */}
        <div
          role="group"
          aria-label="View"
          className="flex overflow-hidden rounded-[9px] border border-line-soft bg-panel-elevated"
        >
          <Button
            type="button"
            size="sm"
            variant={value.view === "grid" ? "secondary" : "ghost"}
            aria-pressed={value.view === "grid"}
            className={cn(
              "h-9 rounded-none border-r border-line-soft px-2.5 transition-colors",
              value.view === "grid" && "bg-accent/60 text-foreground",
            )}
            onClick={() => onChange({ view: "grid" })}
          >
            <LayoutGrid className="size-4" aria-hidden />
            <span className="sr-only">Grid view</span>
          </Button>
          <Button
            type="button"
            size="sm"
            variant={value.view === "list" ? "secondary" : "ghost"}
            aria-pressed={value.view === "list"}
            className={cn(
              "h-9 rounded-none px-2.5 transition-colors",
              canUseTable && "border-line-soft border-r",
              value.view === "list" && "bg-accent/60 text-foreground",
            )}
            onClick={() => onChange({ view: "list" })}
          >
            <List className="size-4" aria-hidden />
            <span className="sr-only">List view</span>
          </Button>
          {canUseTable && (
            <Button
              type="button"
              size="sm"
              variant={value.view === "table" ? "secondary" : "ghost"}
              aria-pressed={value.view === "table"}
              className={cn(
                "h-9 rounded-none px-2.5 transition-colors",
                value.view === "table" && "bg-accent/60 text-foreground",
              )}
              onClick={() => onChange({ view: "table" })}
            >
              <Table2 className="size-4" aria-hidden />
              <span className="sr-only">Table view</span>
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
