"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, FolderKanban, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  substringFilter,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { TRACK_META } from "@/lib/tracks";
import type { Project } from "@/lib/schemas";

export interface ProjectPickerProps {
  value: string | null;
  onValueChange: (projectId: string) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Which project the Orchestrator is driving.
 *
 * The list comes from `GET /projects`, which is already access-scope filtered
 * server-side, so this never has to re-derive the boundary — an empty list here
 * means "no projects you can open", not "no projects".
 *
 * Grouped by delivery track because the track decides the agent roster the run
 * will execute; seeing "Track 3 · Modernization" next to the name is what tells
 * you a ten-stage run is about to start rather than an eight-stage one.
 *
 * A Popover + `cmdk` combobox rather than a `Select`: the list is up to 100
 * entries spread across five track groups, which is well past the point where
 * scrolling beats typing. `cmdk` filters on each item's `value`, so that string
 * carries the track label as well as the name — "modernization" finds every
 * project on Track 3 even though the track appears only in the group heading.
 */
export function ProjectPicker({ value, onValueChange, disabled, className }: ProjectPickerProps) {
  const [open, setOpen] = React.useState(false);

  const projectsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 100 }),
    // 100 is comfortably past any realistic project count for one viewer; the
    // picker is a picker, not a browsable list — /projects is that.
    queryFn: () => listProjects({ pageSize: 100 }),
    staleTime: 30_000,
  });

  const projects = React.useMemo(() => projectsQ.data?.items ?? [], [projectsQ.data]);

  const grouped = React.useMemo(() => {
    const by = new Map<string, Project[]>();
    for (const p of projects) {
      const list = by.get(p.track) ?? [];
      list.push(p);
      by.set(p.track, list);
    }
    return [...by.entries()].sort(
      (a, b) =>
        TRACK_META[a[0] as Project["track"]].number - TRACK_META[b[0] as Project["track"]].number,
    );
  }, [projects]);

  if (projectsQ.isLoading) {
    return (
      <div
        className="border-line-soft bg-surface-1 text-muted-foreground inline-flex h-8 min-w-[210px] items-center gap-2 rounded-md border px-3 text-[12.5px]"
        aria-busy
      >
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        Loading projects…
      </div>
    );
  }

  if (!projectsQ.isError && projects.length === 0) {
    return (
      <Link
        href="/projects"
        className="border-line-soft bg-surface-1 text-muted-foreground hover:text-foreground hover:border-border inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-[12px] transition-colors"
      >
        <FolderKanban className="size-3.5 shrink-0" aria-hidden />
        No projects you can open
      </Link>
    );
  }

  const selected = projects.find((p) => String(p.id) === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="Project"
          disabled={disabled || projectsQ.isError}
          className={cn(
            "border-line-soft bg-surface-1 h-8 w-auto min-w-[210px] max-w-[340px] justify-between gap-2 px-3 text-[12.5px] font-normal",
            className,
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <FolderKanban className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
            <span className="truncate text-[12.5px]">
              {selected ? (
                <>
                  <span className="font-medium">{selected.name}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    · T{TRACK_META[selected.track].number}
                  </span>
                </>
              ) : (
                "Select project"
              )}
            </span>
          </span>
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" aria-hidden />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-[min(22rem,90vw)] p-0" align="start">
        <Command filter={substringFilter}>
          <CommandInput placeholder="Search projects…" />
          <CommandList className="max-h-[min(60vh,20rem)]">
            <CommandEmpty>No matching project.</CommandEmpty>
            {grouped.map(([track, list]) => {
              const meta = TRACK_META[track as Project["track"]];
              return (
                <CommandGroup
                  key={track}
                  heading={
                    <span className="flex items-center gap-2">
                      <span className="text-muted-foreground/70 font-mono text-[10px] tracking-wide uppercase">
                        Track {meta.number}
                      </span>
                      <span className="truncate font-semibold">{meta.shortLabel}</span>
                    </span>
                  }
                >
                  {list.map((p) => {
                    const id = String(p.id);
                    return (
                      <CommandItem
                        key={id}
                        // The searchable string, so it carries the track as
                        // well as the name: the track appears only in the group
                        // heading, and "modernization" should still find the
                        // projects under it. Safe to widen like this only
                        // because `substringFilter` is exact — under cmdk's
                        // default fuzzy scorer the extra words manufacture
                        // false matches.
                        //
                        // The id is appended because names are not unique
                        // across Business Units and cmdk tracks the selected
                        // item by its value string; two same-named projects
                        // would otherwise highlight as one.
                        value={`${p.name} ${meta.label} ${meta.shortLabel} ${id}`}
                        onSelect={() => {
                          onValueChange(id);
                          setOpen(false);
                        }}
                      >
                        <Check
                          className={cn(
                            "size-3.5 shrink-0",
                            id === value ? "opacity-100" : "opacity-0",
                          )}
                          aria-hidden
                        />
                        <span className="truncate text-[12.5px] font-medium">{p.name}</span>
                        <span className="text-muted-foreground ml-auto shrink-0 font-mono text-[10px]">
                          {p.pipeline.length} stages
                        </span>
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              );
            })}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
