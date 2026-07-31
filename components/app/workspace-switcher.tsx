"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Plus, Settings2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PwcMark } from "@/components/brand/pwc-mark";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

export interface WorkspaceSwitcherProps {
  collapsed?: boolean;
}

/**
 * Workspace context selector. The trigger shows the ACTIVE workspace (the
 * business unit you're acting in) with the organization as context underneath —
 * so "which workspace am I in?" is always answerable from the chrome.
 */
export function WorkspaceSwitcher({ collapsed }: WorkspaceSwitcherProps) {
  const session = useSession({ required: true });
  const { tenant } = session;
  const router = useRouter();
  const queryClient = useQueryClient();
  const { active, workspaces, isLoading, isError, refetch, setActive } = useActiveWorkspace();
  const canManage = hasPermission(session, "workspace:manage");
  // Creating a unit is an organization-level act, and `workspace:manage` does
  // not distinguish it: a Business Unit Admin holds that permission for the
  // unit they run, so the permission alone let them create siblings. Scope is
  // what separates "administers a unit" from "administers the org" — the same
  // rule the Business Units page applies to its own create button.
  const { isOrgWide } = useAccessScope();
  const canCreate = canManage && isOrgWide;

  // Self-heal: if the persisted active id is stale (e.g. an old mock id) and we
  // fell back to a real workspace, sync the store + cookie to the real id.
  const activeId = active?.id;
  React.useEffect(() => {
    if (activeId) setActive(activeId);
  }, [activeId, setActive]);

  // Switching workspace re-scopes every server query (the cookie the store wrote
  // is forwarded by the BFF), so invalidate the cache to refetch under the new scope.
  const switchTo = React.useCallback(
    (id: string) => {
      setActive(id);
      queryClient.invalidateQueries();
    },
    [setActive, queryClient],
  );

  const orgName = tenant.name;
  const triggerTitle = active?.displayName ?? orgName;
  const triggerSubtitle = active
    ? orgName
    : isLoading
      ? "Loading…"
      : isError
        ? "Unavailable"
        : `No ${BUSINESS_UNIT_LABEL.toLowerCase()}`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className={cn(
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground h-auto w-full justify-start gap-2 px-2 py-2 text-left",
            collapsed && "justify-center px-0",
          )}
          aria-label={`Switch ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
        >
          <span className="bg-brand-gradient grid size-7 shrink-0 place-items-center overflow-hidden rounded-md shadow-[0_4px_14px_-4px_oklch(0.6_0.2_35_/_0.4)]">
            <PwcMark size={22} />
          </span>
          {!collapsed && (
            <>
              <div className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="font-display truncate text-sm font-semibold">{triggerTitle}</span>
                <span className="text-muted-foreground truncate font-mono text-[10px] tracking-wider uppercase">
                  {triggerSubtitle}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto size-4 opacity-50" aria-hidden />
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="border-line-soft bg-panel-elevated w-64">
        <DropdownMenuLabel className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
          {orgName} · {BUSINESS_UNIT_LABEL_PLURAL}
        </DropdownMenuLabel>
        {isLoading && (
          <div className="text-muted-foreground px-2 py-1.5 text-sm">
            Loading {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}…
          </div>
        )}
        {isError && (
          <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-[13px]">
            <span className="text-muted-foreground">
              Couldn&apos;t load {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}.
            </span>
            <button
              onClick={() => refetch()}
              className="text-brand-bright shrink-0 underline underline-offset-2"
            >
              Retry
            </button>
          </div>
        )}
        {!isLoading && !isError && workspaces.length === 0 && (
          <div className="text-muted-foreground px-2 py-1.5 text-[13px]">
            No {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} yet.
          </div>
        )}
        {workspaces.map((ws) => (
          <DropdownMenuItem key={ws.id} onSelect={() => switchTo(ws.id)}>
            <div className="flex min-w-0 flex-1 flex-col leading-tight">
              <span className="font-display truncate font-semibold">{ws.displayName}</span>
              {ws.businessUnit && (
                <span className="text-muted-foreground truncate font-mono text-[10px] tracking-wider uppercase">
                  {ws.businessUnit}
                </span>
              )}
            </div>
            {active?.id === ws.id && (
              <Check className="text-brand-bright size-4 shrink-0" aria-hidden />
            )}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator className="bg-line-soft" />
        {canCreate && (
          <DropdownMenuItem onSelect={() => router.push("/workspaces?new=1")}>
            <Plus className="size-4" aria-hidden />
            Create {BUSINESS_UNIT_LABEL.toLowerCase()}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={() => router.push("/workspaces")}>
          <Settings2 className="size-4" aria-hidden />
          {canManage
            ? `Manage ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`
            : `My ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
