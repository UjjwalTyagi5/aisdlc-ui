"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronRight, Globe, Plus, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
  substringFilter,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { qk } from "@/lib/api/query-keys";
import {
  grantIntegrationAccess,
  listIntegrationAccess,
  revokeIntegrationAccess,
} from "@/lib/api/integration-access";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { PHASE_LABEL } from "@/lib/agents";
import type { Phase } from "@/lib/schemas/enums";
import type { AccessUnitEntry } from "@/lib/api/integration-access";

/**
 * Which Business Units hold ONE integration, and which of their projects use it.
 *
 * TWO LEVELS, ONE COLLAPSED. The units are the answer to the question that
 * brings an Org Admin here — "who can reach Azure DevOps" — and the projects
 * under each are the follow-up, asked about one unit at a time. Showing every
 * project at once turned a three-unit answer into a twenty-row list and buried
 * the level being governed.
 *
 * Revocation sits on the row it describes: taking the integration from a unit
 * is next to the unit, taking it from a project is next to the project. A
 * single control that changed meaning with a dropdown would be one control for
 * two different decisions.
 */
export function UnitAccessList({
  kind,
  targetId,
  name,
  canRevoke,
  canRevokeProject = true,
}: {
  kind: "connector" | "mcp";
  /** Connector kind or MCP server id. */
  targetId: string;
  /** Display name, for the confirmation copy. */
  name: string;
  /** Organization Admin: may take it away from a whole unit. */
  canRevoke: boolean;
  /** Either admin tier, bounded server-side to their own units. */
  canRevokeProject?: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState<Set<string>>(new Set());

  const accessQ = useQuery({
    queryKey: qk.integrationAccess.list(),
    queryFn: () => listIntegrationAccess(),
  });

  const row = (accessQ.data ?? []).find((r) => r.kind === kind && r.id === targetId);

  const invalidate = () => {
    // Grants decide what every downstream list may show.
    queryClient.invalidateQueries({ queryKey: ["integration-access"] });
    queryClient.invalidateQueries({ queryKey: ["connectors"] });
    queryClient.invalidateQueries({ queryKey: ["mcp"] });
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  };

  const grant = useMutation({
    mutationFn: grantIntegrationAccess,
    onSuccess: (_r, vars) => {
      toast.success(`${vars.unitName} can now use ${name}`);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const revoke = useMutation({
    mutationFn: revokeIntegrationAccess,
    onSuccess: (_r, vars) => {
      toast.success(
        vars.level === "unit"
          ? `${name} removed from ${vars.unitName}`
          : `${vars.projectName} no longer uses ${name}`,
      );
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (accessQ.isLoading) return <LoadingState variant="list" rows={3} />;

  if (!row || row.units.length === 0) {
    return (
      <p className="text-muted-foreground text-[12.5px]">
        There are no {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} to grant this to.
      </p>
    );
  }

  // Held first, then the rest. The ones without it are still listed — that is
  // how a unit is added after it was created, and hiding them left the screen
  // able to take access away and never give it.
  const held = row.units.filter((u) => u.via !== "none");
  const notHeld = row.units.filter((u) => u.via === "none");

  return (
    <ul className="space-y-2">
      {held.map((unit) => {
        const expanded = open.has(unit.id);
        return (
          <li key={unit.id} className="border-line-soft overflow-hidden rounded-lg border">
            <div className="flex items-center gap-2 pr-2">
              <button
                type="button"
                onClick={() =>
                  setOpen((prev) => {
                    const next = new Set(prev);
                    if (next.has(unit.id)) next.delete(unit.id);
                    else next.add(unit.id);
                    return next;
                  })
                }
                aria-expanded={expanded}
                aria-label={`${expanded ? "Hide" : "Show"} projects in ${unit.name} using ${name}`}
                className="hover:bg-surface-1/60 flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left transition-colors"
              >
                <ChevronRight
                  className={cn("size-3.5 shrink-0 transition-transform", expanded && "rotate-90")}
                  aria-hidden
                />
                <span className="truncate text-[13px] font-medium">{unit.name}</span>
                <Badge variant="outline" className="shrink-0 gap-1 font-mono text-[10px]">
                  <Globe className="size-3" aria-hidden />
                  granted
                </Badge>
                <span className="text-muted-foreground ml-auto shrink-0 text-[11.5px]">
                  {(() => {
                    const using = unit.projects.filter((p) => p.stages.length > 0).length;
                    return using === 0
                      ? "No project uses it"
                      : `${using} ${using === 1 ? "project" : "projects"}`;
                  })()}
                </span>
              </button>

              {canRevoke && (
                <RevokeButton
                  label={`Remove ${name} from ${unit.name}`}
                  confirm={`Remove ${name} from ${unit.name}? Its ${unit.projects.length} project${unit.projects.length === 1 ? "" : "s"} using it will lose it.`}
                  busy={revoke.isPending}
                  onConfirm={() =>
                    revoke.mutate({
                      kind,
                      id: targetId,
                      level: "unit",
                      workspaceId: unit.id,
                      unitName: unit.name,
                    })
                  }
                />
              )}
            </div>

            {expanded && (
              <div className="border-line-soft space-y-2 border-t px-3 py-2">
                {unit.projects.filter((p) => p.stages.length > 0).length === 0 ? (
                  <p className="text-muted-foreground text-[11.5px]">
                    Granted, and no project in {unit.name} has wired it up. A project switches it
                    on in its own Settings → Tools per stage.
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {unit.projects
                      .filter((p) => p.stages.length > 0)
                      .map((p) => (
                      <li key={p.id} className="flex flex-wrap items-center gap-x-2 text-[12px]">
                        <Link
                          href={`/projects/${encodeURIComponent(p.id)}/integrations`}
                          className="text-brand-bright underline underline-offset-2"
                        >
                          {p.name}
                        </Link>
                        {p.stages.length > 0 && (
                          <span className="text-muted-foreground text-[11px]">
                            {p.stages.map((s) => PHASE_LABEL[s as Phase] ?? s).join(" · ")}
                          </span>
                        )}
                        {canRevokeProject && (
                          <span className="ml-auto">
                            <RevokeButton
                              label={`Remove ${name} from ${p.name}`}
                              confirm={`Stop ${p.name} using ${name}? Its agents lose it on every stage. ${unit.name} keeps the grant.`}
                              busy={revoke.isPending}
                              onConfirm={() =>
                                revoke.mutate({
                                  kind,
                                  id: targetId,
                                  level: "project",
                                  projectId: p.id,
                                  projectName: p.name,
                                })
                              }
                            />
                          </span>
                        )}
                      </li>
                      ))}
                  </ul>
                )}

              </div>
            )}
          </li>
        );
      })}
      {/* ALWAYS rendered for an Org Admin, even with nothing left to grant.
          A control that only appears when it has options is a control nobody
          can find: with every unit already holding the integration the screen
          showed only Revoke, and "how do I add one" had no answer on it. */}
      {canRevoke && (
        <li>
          <GrantUnitPicker
            units={notHeld}
            name={name}
            busy={grant.isPending}
            onGrant={(u) =>
              grant.mutate({ kind, id: targetId, workspaceId: u.id, unitName: u.name })
            }
          />
        </li>
      )}

      <li className="text-muted-foreground pt-1 text-[11.5px]">
        {row.grantedUnitCount}{" "}
        {row.grantedUnitCount === 1
          ? BUSINESS_UNIT_LABEL.toLowerCase()
          : BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}
        {" · "}
        {row.projectCount} {row.projectCount === 1 ? "project" : "projects"} using it
      </li>
    </ul>
  );
}

/**
 * The one place a Business Unit is given an integration.
 *
 * A searchable popover rather than a row of buttons: the unit list is the
 * thing that grows, and at twenty units a wrapped row of chips is no longer
 * something you can find a name in. Disabled-with-a-reason when every unit
 * already holds it, because a control that vanishes teaches nobody where it
 * went.
 */
function GrantUnitPicker({
  units,
  name,
  busy,
  onGrant,
}: {
  units: AccessUnitEntry[];
  name: string;
  busy?: boolean;
  onGrant: (unit: AccessUnitEntry) => void;
}) {
  const [open, setOpen] = React.useState(false);

  if (units.length === 0) {
    return (
      <p className="text-muted-foreground text-[11.5px]">
        Every {BUSINESS_UNIT_LABEL.toLowerCase()} already has {name}.
      </p>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          className="border-line-soft h-8 gap-1.5 border-dashed text-[12px]"
        >
          <Plus className="size-3.5" aria-hidden />
          Grant to a {BUSINESS_UNIT_LABEL.toLowerCase()}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-0">
        <Command filter={substringFilter}>
          <CommandInput placeholder={`Search ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}…`} />
          <CommandList>
            <CommandEmpty>No {BUSINESS_UNIT_LABEL.toLowerCase()} matches.</CommandEmpty>
            {units.map((u) => (
              <CommandItem
                key={u.id}
                value={u.name}
                onSelect={() => {
                  setOpen(false);
                  onGrant(u);
                }}
              >
                <Plus className="size-3.5" aria-hidden />
                {u.name}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Revoke, with the consequence stated before it happens.
 *
 * Two clicks rather than a modal: the second click IS the confirmation, and
 * the tooltip carries what a dialog's body would have said. A modal per row
 * would be more ceremony than the action deserves; a one-click silent revoke
 * would be less than it deserves.
 */
function RevokeButton({
  label,
  confirm,
  busy,
  onConfirm,
}: {
  label: string;
  confirm: string;
  busy?: boolean;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = React.useState(false);

  // Disarms itself, so a half-pressed button never sits waiting to fire.
  React.useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            aria-label={armed ? `Confirm: ${label}` : label}
            onClick={(e) => {
              e.stopPropagation();
              if (armed) onConfirm();
              else setArmed(true);
            }}
            onBlur={() => setArmed(false)}
            className={cn(
              "h-6 shrink-0 gap-1 px-1.5 text-[11px]",
              armed
                ? "text-destructive hover:text-destructive font-medium"
                : "text-muted-foreground hover:text-destructive",
            )}
          >
            <X className="size-3" aria-hidden />
            {armed ? "Confirm" : "Revoke"}
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-[280px]">{confirm}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
