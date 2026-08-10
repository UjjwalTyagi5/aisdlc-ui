"use client";

import * as React from "react";
import { Building2, FolderKanban, Globe2, Lock, ShieldCheck, Wrench } from "lucide-react";

import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { SCOPE_META, type ScopeLevel } from "@/lib/scope";
import { useAccessScope } from "@/hooks/use-access-scope";
import type { ScopeKind } from "@/lib/schemas/access-scope";

/**
 * Scope indicators — the answer to "what am I looking at, and as whom".
 *
 * Once lists are filtered by scope, the UI has a new failure mode: two people
 * see different numbers on the same screen with nothing explaining why, and a
 * Business Unit Admin reading "3 projects" cannot tell whether that is the whole
 * organisation or just their unit. Every filtered surface therefore states its
 * boundary. These are presentation components over `useAccessScope()` — they
 * never gate anything.
 */

const SCOPE_ICON: Record<ScopeKind, typeof Globe2> = {
  organization: Globe2,
  business_unit: Building2,
  project: FolderKanban,
};

/** Maps the scope kinds this module uses onto the PRD's label vocabulary. */
const SCOPE_LEVEL: Record<ScopeKind, ScopeLevel> = {
  organization: "organization",
  business_unit: "business_unit",
  project: "project",
};

export function scopeKindLabel(kind: ScopeKind): string {
  return SCOPE_META[SCOPE_LEVEL[kind]].label;
}

// ─── Scope chip ───────────────────────────────────────────────────────────────

export interface ScopeChipProps {
  kind: ScopeKind;
  /** The scope's name, e.g. "Payments". Omit for an unnamed tier label. */
  name?: string | null;
  /** "read" renders the muted treatment and an explicit read-only tooltip. */
  access?: "manage" | "read";
  className?: string;
  size?: "sm" | "md";
}

/**
 * One scope, named and typed. The type label is never dropped in favour of just
 * the name: "Payments" alone is ambiguous — there is a Payments Business Unit
 * AND a payments-api project — and that ambiguity is precisely what a scoped
 * viewer cannot afford to guess at.
 */
export function ScopeChip({
  kind,
  name,
  access = "manage",
  className,
  size = "md",
}: ScopeChipProps) {
  /**
   * An unnamed organization scope renders NOTHING.
   *
   * The chip exists to stop a filtered number being misread as the whole — a
   * Business Unit Admin seeing "3 projects" has to know it means their unit.
   * An org-wide viewer has no narrower scope to confuse it with: everything
   * they see is everything there is, so "ORGANIZATION" restated the default on
   * every screen and taught nobody anything.
   *
   * A NAMED organization scope still renders, because a name is information.
   * The rule is about the bare tier label, not about the tier.
   */
  if (kind === "organization" && !name) return null;

  const Icon = SCOPE_ICON[kind];
  const label = scopeKindLabel(kind);

  const chip = (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-0.5",
        size === "sm" ? "text-[10.5px]" : "text-[11.5px]",
        access === "manage"
          ? "border-brand-bright/35 bg-brand-bright/10 text-brand-bright"
          : "border-line-soft bg-surface-1 text-muted-foreground",
        className,
      )}
    >
      <Icon className={cn("shrink-0", size === "sm" ? "size-3" : "size-3.5")} aria-hidden />
      <span className="font-mono tracking-wide uppercase opacity-70">{label}</span>
      {name && (
        <>
          <span className="opacity-40" aria-hidden>
            /
          </span>
          <span className="truncate font-medium">{name}</span>
        </>
      )}
      {access === "read" && <Lock className="size-2.5 shrink-0 opacity-60" aria-hidden />}
    </span>
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>{chip}</TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[280px]">
        <p className="font-medium">
          {label}
          {name ? ` · ${name}` : ""}
        </p>
        <p className="text-muted-foreground mt-1 text-[12px]">
          {access === "manage"
            ? SCOPE_META[SCOPE_LEVEL[kind]].purpose
            : "Visible for context only — you can read this scope but not administer it."}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

// ─── Persona badge ────────────────────────────────────────────────────────────

/**
 * The role the viewer is acting as. Governance roles carry the shield and the
 * brand tint, delivery roles the wrench and a muted one — so the tier is legible
 * without reading the label, which matters now that the same person legitimately
 * holds both in different scopes ([[per-scope-tier-separation]]).
 */
export function PersonaBadge({
  role,
  className,
  showScope = false,
}: {
  role: PlatformRole | null;
  className?: string;
  showScope?: boolean;
}) {
  if (!role) return null;
  const meta = ROLE_META[role];
  const governance = meta.tier === "governance";
  const Icon = governance ? ShieldCheck : Wrench;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
            governance
              ? "border-brand-bright/35 bg-brand-bright/10 text-brand-bright"
              : "border-line-soft bg-surface-1 text-muted-foreground",
            className,
          )}
        >
          <Icon className="size-3 shrink-0" aria-hidden />
          {meta.shortLabel}
          {showScope && (
            <span className="font-mono text-[9.5px] tracking-wide uppercase opacity-60">
              {scopeKindLabel(meta.scope === "configurable" ? "project" : meta.scope)}
            </span>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[300px]">
        <p className="font-medium">{meta.label}</p>
        <p className="text-muted-foreground mt-1 text-[12px]">{meta.oneLiner}</p>
        <p className="text-muted-foreground mt-1.5 font-mono text-[10.5px] tracking-wide uppercase">
          {governance ? "Governance tier" : "Delivery tier"} · bound at{" "}
          {meta.scope.replace(/_/g, " ")}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

// ─── Composed context bar ─────────────────────────────────────────────────────

/**
 * The viewer's active scope, for the mobile navigation sheet — the one place
 * the desktop sidebar's scope chip has no equivalent.
 *
 * Scope only, no role: the account menu states the role, and repeating it in
 * the chrome is what made it appear three times on a single page header. What
 * the chrome owes the viewer is the BOUNDARY the numbers are drawn inside,
 * because two people otherwise see different counts on the same screen with
 * nothing explaining why. Which of them is a Business Unit Admin is a separate
 * question, and not one the numbers depend on.
 *
 * Renders nothing until the scope resolves rather than flashing a provisional
 * boundary — a chip reading "Organization" for one frame before correcting
 * itself to "Business Unit / Payments" is worse than one that arrives late.
 */
export function ScopeContextBar({ className }: { className?: string }) {
  const { scope, level, isLoading, isError, managedBusinessUnitIds, bindings } =
    useAccessScope();

  if (isLoading || isError || !scope) return null;

  // Name the single scope being managed when there is exactly one; a
  // multi-unit admin gets a count instead of an arbitrary first name.
  const soleUnit =
    managedBusinessUnitIds.length === 1
      ? bindings.find(
          (b) => b.kind === "business_unit" && b.scopeId === managedBusinessUnitIds[0],
        )
      : undefined;
  const projectBindings = bindings.filter((b) => b.kind === "project");
  const soleProject = projectBindings.length === 1 ? projectBindings[0] : undefined;

  const name = scope.isOrgWide
    ? null
    : level === "business_unit"
      ? (soleUnit?.scopeName ?? `${managedBusinessUnitIds.length} business units`)
      : (soleProject?.scopeName ??
        (projectBindings.length > 0 ? `${projectBindings.length} projects` : null));

  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)}>
      <ScopeChip kind={scope.isOrgWide ? "organization" : level} name={name} size="sm" />
    </div>
  );
}
