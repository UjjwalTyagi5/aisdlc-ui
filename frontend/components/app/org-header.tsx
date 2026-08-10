"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ChevronsUpDown, Plus, Settings2 } from "lucide-react";

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
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

export interface OrgHeaderProps {
  collapsed?: boolean;
}

/**
 * What the chrome calls itself. Matches the landing page, the login screen and
 * the document title, so the product has one name rather than four — change it
 * here and in `app/layout.tsx`'s metadata together.
 */
const PRODUCT_NAME = "SDLC Platform";
const PRODUCT_TAGLINE = "AI SDLC";

/**
 * The sidebar's identity header: the PRODUCT, plus the two Business Unit
 * actions for whoever may take them.
 *
 * It names neither a Business Unit nor the tenant. The unit came out first — a
 * switcher whose trigger showed whichever unit happened to resolve first, which
 * made "what am I looking at" a UI setting rather than a consequence of access,
 * and which was simply a lie for anyone bound to more than one unit. The
 * organization name came out next, for a plainer reason: it is the same string
 * on every screen for every person in the tenant, so it spends the most
 * valuable strip of chrome on something nobody needs told. The scope a viewer
 * is actually working in is on the ScopeChip below and in the top bar, where it
 * changes and therefore carries information.
 *
 * What survives is the pair of actions that were buried under that dropdown:
 * creating a unit (an organization-level act) and reaching the Business Units
 * page. The list between them is gone.
 */
export function OrgHeader({ collapsed }: OrgHeaderProps) {
  const session = useSession({ required: true });
  const router = useRouter();
  const { isOrgWide } = useAccessScope();

  // The same gate the Business Units nav item uses, so the menu can never offer
  // a page its own sidebar link withholds.
  const canManage = hasPermission(session, "workspace:manage");
  // Creating a unit is an organization-level act, and `workspace:manage` does
  // not distinguish it: a Business Unit Admin holds that permission for the
  // unit they run, so the permission alone would let them create siblings.
  const canCreate = canManage && isOrgWide;

  const identity = (
    <>
      <span className="bg-brand-gradient grid size-7 shrink-0 place-items-center overflow-hidden rounded-md shadow-[0_4px_14px_-4px_oklch(0.6_0.2_35_/_0.4)]">
        <PwcMark size={22} />
      </span>
      {!collapsed && (
        <div className="flex min-w-0 flex-1 flex-col leading-tight">
          <span className="font-display truncate text-sm font-semibold">{PRODUCT_NAME}</span>
          <span className="text-muted-foreground truncate font-mono text-[10px] tracking-wider uppercase">
            {PRODUCT_TAGLINE}
          </span>
        </div>
      )}
    </>
  );

  // Nothing to offer → a plain header rather than a dropdown that opens onto a
  // single dead item.
  if (!canManage) {
    return (
      <div
        className={cn(
          "flex h-auto w-full items-center gap-2 px-2 py-2 text-left",
          collapsed && "justify-center px-0",
        )}
      >
        {identity}
      </div>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className={cn(
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground h-auto w-full justify-start gap-2 px-2 py-2 text-left",
            collapsed && "justify-center px-0",
          )}
          aria-label={`${BUSINESS_UNIT_LABEL} actions`}
        >
          {identity}
          {!collapsed && <ChevronsUpDown className="ml-auto size-4 opacity-50" aria-hidden />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="border-line-soft bg-panel-elevated w-64">
        <DropdownMenuLabel className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
          {BUSINESS_UNIT_LABEL_PLURAL}
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-line-soft" />
        {canCreate && (
          <DropdownMenuItem onSelect={() => router.push("/workspaces?new=1")}>
            <Plus className="size-4" aria-hidden />
            Create {BUSINESS_UNIT_LABEL.toLowerCase()}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={() => router.push("/workspaces")}>
          <Settings2 className="size-4" aria-hidden />
          {isOrgWide
            ? `Manage ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`
            : `My ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
