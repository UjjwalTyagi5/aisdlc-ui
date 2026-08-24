"use client";

import * as React from "react";
import Link from "next/link";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronRight, Plus, Search, SlidersHorizontal } from "lucide-react";

import { cn } from "@/lib/utils";
import { PageTitle } from "@/components/app/page-title";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/loading-state";
import {
  ModelGovernanceSummary,
  countModelGovernance,
} from "@/components/app/model-governance-summary";
import { ModelAvailabilityCard } from "@/components/app/model-availability-card";
import { AddModelDialog, filterCatalogToAllowed } from "@/components/app/add-model-dialog";
import { ProviderModelCurationDialog } from "@/components/app/provider-model-curation-dialog";
import { UnitAccessPicker } from "@/components/app/unit-access-picker";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useRawSession } from "@/components/auth/session-provider";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { getSpendSeries } from "@/lib/api/cost";
import {
  getBuAllowedModels,
  getModelCatalog,
  getModelGrantMatrix,
  listAllModelProviders,
  listModelProviderGrants,
  listModelProviders,
  setModelProviderGrants,
} from "@/lib/api/models";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { qk } from "@/lib/api/query-keys";
import { providerLabel } from "@/lib/models/provider-labels";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { CatalogProvider, ModelAllowEntry, ModelProvider } from "@/lib/schemas/model";

/** Real brand logos (in public/brand), same scheme as the Integrations page. */
const PROVIDER_LOGO: Partial<Record<string, { src: string; fit: "contain" | "cover-left" }>> = {
  anthropic: { src: "/brand/anthropic.svg", fit: "contain" },
  google: { src: "/brand/google.svg", fit: "contain" },
  vertex_ai: { src: "/brand/google.svg", fit: "contain" },
  azure: { src: "/brand/azure.png", fit: "contain" },
};

/** Brand-colored monogram for a provider with no logo file on hand. */
const PROVIDER_BRAND: Record<string, { mark: string; bg: string }> = {
  openai: { mark: "AI", bg: "#000000" }, // OpenAI black
  bedrock: { mark: "AWS", bg: "#FF9900" }, // AWS orange
  xai: { mark: "X", bg: "#000000" }, // xAI black
  mistral: { mark: "M", bg: "#FA5210" }, // Mistral orange
  cohere: { mark: "C", bg: "#39594D" }, // Cohere forest
};

function ProviderGlyph({ kind, label }: { kind: string; label: string }) {
  const logo = PROVIDER_LOGO[kind];
  if (logo) {
    return (
      <div
        aria-hidden
        className="border-line-soft grid size-10 shrink-0 place-items-center overflow-hidden rounded-lg border bg-white shadow-[0_1px_0_oklch(1_0_0_/_0.22)_inset,0_2px_8px_-3px_oklch(0_0_0_/_0.5)]"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- small static brand asset; <img> renders SVG+PNG uniformly without next/image SVG config */}
        <img
          src={logo.src}
          alt=""
          aria-hidden
          className={cn(
            "size-full",
            logo.fit === "cover-left" ? "object-cover object-left" : "object-contain p-2",
          )}
        />
      </div>
    );
  }
  const brand = PROVIDER_BRAND[kind];
  if (brand) {
    return (
      <div
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-lg font-mono text-[13px] font-bold tracking-tight text-white shadow-[0_1px_0_oklch(1_0_0_/_0.22)_inset,0_2px_8px_-3px_oklch(0_0_0_/_0.5)]"
        style={{ backgroundColor: brand.bg }}
      >
        {brand.mark}
      </div>
    );
  }
  return (
    <div
      aria-hidden
      className="border-line-soft bg-surface-2 text-muted-foreground grid size-10 shrink-0 place-items-center rounded-lg border font-mono text-base font-semibold"
    >
      {label.charAt(0)}
    </div>
  );
}

export default function ModelProvidersPage() {
  const queryClient = useQueryClient();
  const session = useRawSession();
  const role = effectivePlatformRole(session);

  // The cascade's three vantage points: an Org Admin's own onboarding is
  // org-wide (workspaceId null); a BU Admin's and a Project Admin's land inside
  // a business unit they're bound to.
  const scope: "org" | "bu" | "project" | null =
    role === "org_admin" ? "org" : role === "bu_admin" ? "bu" : role === "project_admin" ? "project" : null;
  const isOrg = scope === "org";
  const needsApproval = scope === "project";

  // Which units this page speaks for. There is no "active" one to inherit —
  // reads union across every unit the viewer is bound to, so someone in two
  // units sees both rather than whichever the old switcher happened to leave
  // selected. See hooks/use-scoped-business-units.ts.
  const { units: scopedUnits, isLoading: unitsLoading } = useScopedBusinessUnits();

  const providerQueries = useQueries({
    queries: isOrg
      ? [
          {
            // EVERY connection, not just org-wide. Asking for one scope made a
            // subscription onboarded by a Business Unit invisible here, so the
            // page answered "which providers are we using" with only half.
            queryKey: qk.model.providers("all"),
            queryFn: () => listAllModelProviders(),
            staleTime: 0,
          },
        ]
      : scopedUnits.map((u) => ({
          queryKey: qk.model.providers(u.id),
          queryFn: () => listModelProviders(u.id),
          staleTime: 0,
        })),
  });

  /**
   * This month's spend per provider — rendered ON each card rather than in a
   * chart above them. A separate bar chart repeated the provider list and put
   * the number furthest from the thing it describes; a provider is what you
   * onboard, credential and pay for, so the figure belongs on its card.
   */
  const spendQ = useQuery({
    queryKey: qk.cost.spendSeries("provider", "all", 6),
    queryFn: () => getSpendSeries({ groupBy: "provider", months: 6 }),
    staleTime: 60_000,
  });
  const spendByProvider = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const entry of spendQ.data?.series ?? []) {
      // Last point is the current month — the same figure Cost & Budget
      // prints, so the two cannot disagree.
      m.set(entry.id, entry.points[entry.points.length - 1] ?? 0);
    }
    return m;
  }, [spendQ.data]);

  const catalogQ = useQuery({
    queryKey: qk.model.catalog(),
    queryFn: () => getModelCatalog(),
  });

  // The estate's shape, for the summary row. Org Admin only — the matrix is
  // every unit's standing against every model, which is precisely what a BU or
  // Project Admin may not see.
  const matrixQ = useQuery({
    queryKey: qk.model.grantMatrix(),
    queryFn: getModelGrantMatrix,
    enabled: isOrg,
  });
  const governance = React.useMemo(
    () => countModelGovernance(matrixQ.data?.rows ?? []),
    [matrixQ.data],
  );

  // Which business units may create their own connection to each provider —
  // the Org Admin's grant-toggle on each card. Org Admin only: a BU/Project
  // Admin doesn't grant, it consumes what's already granted.
  const providerGrantsQ = useQuery({
    queryKey: qk.model.providerGrants(),
    queryFn: listModelProviderGrants,
    enabled: isOrg,
  });

  // What each unit was granted — the universe a BU or Project Admin may
  // credential from, per unit. An Org Admin doesn't need it: they define it.
  const allowedQueries = useQueries({
    queries: isOrg
      ? []
      : scopedUnits.map((u) => ({
          queryKey: qk.model.buAllowed(u.id),
          queryFn: () => getBuAllowedModels(u.id),
        })),
  });
  const allowedByUnit = React.useMemo(() => {
    const map: Record<string, ModelAllowEntry[]> = {};
    scopedUnits.forEach((u, i) => {
      map[u.id] = allowedQueries[i]?.data ?? [];
    });
    return map;
    // allowedQueries is a fresh array each render; its data is what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopedUnits, allowedQueries.map((q) => q.dataUpdatedAt).join(",")]);

  // Only an Org Admin picks who a newly credentialed model reaches, so only
  // their dialog needs the full unit list.
  const { data: allWorkspaces } = useWorkspaces();
  const grantableWorkspaces = React.useMemo(
    () => (allWorkspaces ?? []).filter((w) => w.status === "active"),
    [allWorkspaces],
  );
  // UnitAccessPicker's generic {id, name} shape — the provider-grant toggle
  // and the model-curation dialog both need it, `displayName` is what
  // `Workspace` calls the same field.
  const grantUnits = React.useMemo(
    () => grantableWorkspaces.map((w) => ({ id: w.id, name: w.displayName })),
    [grantableWorkspaces],
  );

  // providerGrantsQ's shape is provider→units; the PUT route replaces one
  // WORKSPACE's whole provider list at a time (Task 3's contract), so toggling
  // one provider for one workspace needs that workspace's full current list,
  // which means inverting the shape first.
  const grantsByWorkspace = React.useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const g of providerGrantsQ.data ?? []) {
      for (const wsId of g.businessUnitIds) {
        (map[wsId] ??= []).push(g.provider);
      }
    }
    return map;
  }, [providerGrantsQ.data]);

  const toggleProviderGrant = async (provider: string, workspaceId: string) => {
    const current = grantsByWorkspace[workspaceId] ?? [];
    const next = current.includes(provider)
      ? current.filter((p) => p !== provider)
      : [...current, provider];
    try {
      await setModelProviderGrants(workspaceId, next);
      queryClient.invalidateQueries({ queryKey: qk.model.providerGrants() });
    } catch (err) {
      toast.error("Couldn't update provider access", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  // No edit/remove/verify state here any more. Those act on a single key and
  // now live on the provider's own screen, which is the only place that also
  // shows what each key serves.
  //
  // `addOpen` is the Project Admin's plain "Add provider" (provider picker,
  // pending approval) — the one call site this dialog still opens with no
  // provider preset. `addKeyProvider` is the BU Admin's "Add key" (spec §5,
  // Task 10): set to a provider slug by clicking THAT provider's own
  // granted-but-unkeyed tile, never by a page-level button — there is no
  // provider picker in that flow at all, so there is nothing for a bare
  // button with no tile behind it to open.
  const [addOpen, setAddOpen] = React.useState(false);
  const [addKeyProvider, setAddKeyProvider] = React.useState<string | null>(null);
  const [query, setQuery] = React.useState("");

  // Prefix-invalidate: with one query per unit there is no single key to name,
  // and a provider added to one unit still changes this page's totals.
  const invalidateProviders = () =>
    queryClient.invalidateQueries({ queryKey: ["model", "providers"] });

  // Wait for the scope too: rendering "no units" before the bindings resolve
  // is a false access-denied, the same three-state rule the sidebar follows.
  if (unitsLoading || providerQueries.some((q) => q.isLoading)) {
    return (
      <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
        <LoadingState variant="list" rows={3} />
      </div>
    );
  }

  const failed = providerQueries.find((q) => q.isError);
  if (failed) {
    return (
      <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
        <ApiErrorState
          title="Couldn't load model providers"
          error={
            failed.error && "code" in failed.error && "message" in failed.error
              ? (failed.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(failed.error && "code" in failed.error)
              ? failed.error instanceof Error
                ? failed.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => providerQueries.forEach((q) => void q.refetch())}
        />
      </div>
    );
  }

  if (!hasPermission(session, "model:manage") || !scope) {
    return (
      <RestrictedAccess description="Model providers require the model:manage permission." />
    );
  }

  // One flat list across every unit in view; each provider still knows which
  // unit it belongs to, so the cards can say so when there is more than one.
  const providers = providerQueries.flatMap((q) => q.data ?? []);

  // Needed before providerGroups below (Org Admin's grid is catalog-seeded);
  // moved up from where it used to sit purely for `effectiveCatalog`.
  const catalog = catalogQ.data ?? [];

  /**
   * One card per PROVIDER, not per connection.
   *
   * A subscription is a credential, not a provider. Rendering a card each gave
   * two "Anthropic" tiles that both claimed the provider's whole spend — the
   * figure is keyed by provider, so splitting the card double-counted it — and
   * split one vendor's models across two places for no reason a reader could
   * see. The subscriptions live on the detail screen, which already lists them
   * and is where testing, editing and removing a key belongs.
   *
   * FOR AN ORG ADMIN THE GRID IS CATALOG-SEEDED, not connection-seeded. "Add
   * provider" is gone from their flow entirely (Step 2 above) — granting a
   * business unit reach to a provider and curating its models (the card's own
   * controls, see `ProviderCard`) is now the ONLY thing an Org Admin does with
   * a provider here, and both of those act on a catalog SLUG, not on an
   * existing `model_providers` connection row (`PUT /model/providers/grants`
   * accepts any catalog provider). A brand-new tenant has zero connections
   * anywhere, so building this grid from connections alone would leave an Org
   * Admin with no card — and so no way — to grant a provider nobody has ever
   * connected to yet. `!isOrg`'s grid is deliberately NOT catalog-seeded: a BU
   * or Project Admin's screen is about what has actually been onboarded or
   * granted to them, not the abstract catalog.
   */
  const providerGroups = ((): [string, typeof providers][] => {
    // Plain function, not useMemo: this sits below an early return, so a hook
    // here would change hook order between renders. Grouping a handful of
    // connections is far cheaper than the bug that would buy.
    const by = new Map<string, typeof providers>();
    for (const p of providers) {
      const list = by.get(p.provider) ?? [];
      list.push(p);
      by.set(p.provider, list);
    }
    if (!isOrg) {
      // A BU Admin's grid also carries a tile for every provider their Org
      // Admin granted but nobody in this unit has keyed yet (spec §5) — with
      // ZERO connections, `by` alone would never produce one, and "Add key"
      // (Task 10) has nowhere to live without a tile to put it on. An empty
      // array (not omitted) is the signal `ProviderCard` reads to know there's
      // no detail page to link to yet.
      if (scope === "bu") {
        for (const unitAllowed of Object.values(allowedByUnit)) {
          for (const entry of unitAllowed) {
            if (!by.has(entry.provider)) by.set(entry.provider, []);
          }
        }
      }
      return [...by.entries()];
    }
    // Catalog order first — the more legible, canonical ordering — with each
    // provider's real connections (if any) merged in. A connection whose slug
    // is somehow absent from the catalog (should not happen, but "off the
    // catalog" must not mean "invisible") is appended after rather than
    // dropped.
    const seen = new Set<string>();
    const merged: [string, typeof providers][] = [];
    for (const c of catalog) {
      merged.push([c.provider, by.get(c.provider) ?? []]);
      seen.add(c.provider);
    }
    for (const [kind, group] of by) {
      if (!seen.has(kind)) merged.push([kind, group]);
    }
    return merged;
  })();

  /**
   * Search across all three things a provider card stands for: the vendor, the
   * subscriptions under it, and the models those cover.
   *
   * The cards themselves now show only the vendor name — the level this screen
   * is about — but the search still reaches inside them. Matching the visible
   * text alone would answer the question nobody has (you can already read the
   * six names); the questions that bring you here are "which contract is the EU
   * one" and "who serves gpt-5.1", and neither is visible until you drill in.
   * A search that finds what a card does not display is the point.
   *
   * For an Org Admin, "who serves gpt-5.1" now has to be answerable even for a
   * provider nobody has connected to yet — its card carries no offerings to
   * search, only the catalog's own model list, so that's consulted too.
   */
  const q = query.trim().toLowerCase();
  const visibleGroups = q
    ? providerGroups.filter(([kind, group]) => {
        if (providerLabel(kind).toLowerCase().includes(q) || kind.toLowerCase().includes(q)) {
          return true;
        }
        if (
          group.some(
            (p) =>
              p.display_name.toLowerCase().includes(q) ||
              p.offerings.some((o) => o.model_id.toLowerCase().includes(q)),
          )
        ) {
          return true;
        }
        if (isOrg) {
          const catalogModels = catalog.find((c) => c.provider === kind)?.models ?? [];
          if (catalogModels.some((m) => m.model_id.toLowerCase().includes(q))) return true;
        }
        return false;
      })
    : providerGroups;

  // Everything the viewer could credential anywhere they're bound — the union
  // across their units. The dialog narrows this again to the ONE unit being
  // onboarded into, which is the set that actually governs the save.
  const allAllowed = Object.values(allowedByUnit).flat();

  // The onboarding dialog's provider picker is scoped by the cascade: an Org
  // Admin sees the full catalog (they define what's permitted at all), while a
  // BU or Project Admin sees only what the Org Admin granted their units.
  const effectiveCatalog = isOrg ? catalog : filterCatalogToAllowed(catalog, allAllowed);

  // How many models a granted-but-unkeyed provider tile can say it's holding a
  // place for (bu scope only) — there's no connection yet to count offerings
  // from, so this counts the grant itself instead.
  const grantedModelCountByProvider = new Map<string, number>();
  for (const e of allAllowed) {
    grantedModelCountByProvider.set(e.provider, (grantedModelCountByProvider.get(e.provider) ?? 0) + 1);
  }

  const HEADER_COPY: Record<"org" | "bu" | "project", { eyebrow: string; title: string; body: string }> = {
    org: {
      eyebrow: "Control plane",
      title: "Models",
      body: "Approve the models this organization may use, decide which business units get each one, and bring your own LLM keys. Agent runs use these models — and only these.",
    },
    bu: {
      eyebrow: "Control plane",
      title: "Models",
      body: `The models your Organization Admin granted this ${BUSINESS_UNIT_LABEL.toLowerCase()}. Anything they keyed centrally works as-is; for the rest, connect this ${BUSINESS_UNIT_LABEL.toLowerCase()}'s own credentials.`,
    },
    project: {
      eyebrow: "Configure",
      title: "Models",
      body: `The models your business unit was granted. Onboard your own credentials for any that need them — new connections need your ${BUSINESS_UNIT_LABEL} Admin's approval before they're usable.`,
    },
  };
  const copy = HEADER_COPY[scope];

  return (
    <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
      {/* Editorial page header — mirrors integrations hub */}
      <header
        className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div>
          <PageTitle>{copy.title}</PageTitle>
        </div>

        {/* Org Admin no longer credentials anything here — they grant a
            provider to a business unit (per-card toggle below) and curate
            which models of it reach that unit; onboarding an actual key
            moved entirely to each business unit / project's own screen.
            Project Admin keeps this page-level trigger (a provider picker,
            pending BU Admin approval) — only the BU Admin's "Add key" moved
            to a per-card action (spec §5, Task 10): its provider is always
            already fixed by the tile clicked, so a bare page-level button
            with no tile behind it would have nothing to open. */}
        {scope === "project" && (
          <Button
            onClick={() => setAddOpen(true)}
            disabled={effectiveCatalog.length === 0}
            title={effectiveCatalog.length === 0 ? "No allowed models to onboard from yet" : undefined}
            className="from-brand-gradient-from to-brand-gradient-to shrink-0 bg-gradient-to-br font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.65)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.8)]"
          >
            <Plus className="size-4" aria-hidden />
            Add provider
          </Button>
        )}
      </header>

      {/* The estate before the inventory: how many models exist, how far they
          reach, and how many are inert. Above the search deliberately — these
          describe everything, not the filtered view, and a count that moved
          when you typed would read as a filtered total. */}
      {isOrg && matrixQ.data && <ModelGovernanceSummary counts={governance} />}

      {/* One card per unit the viewer is bound to. Someone in two units gets
          two, each named — no arbitrary winner, and no hidden second unit. */}
      {!isOrg && scopedUnits.length === 0 && (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            You aren&apos;t bound to any {BUSINESS_UNIT_LABEL.toLowerCase()} yet, so there are no
            models to configure. Ask an admin to add you to one.
          </p>
        </div>
      )}

      {!isOrg &&
        scopedUnits.map((u) => (
          <ModelAvailabilityCard
            key={u.id}
            workspaceId={u.id}
            workspaceName={u.name}
            audience={scope === "project" ? "project" : "bu"}
            /* The UNFILTERED catalogue, deliberately — `effectiveCatalog` is
               narrowed to what this scope was granted, which is precisely the
               list the card already shows. What it needs on top is the rest. */
            catalog={catalog}
          />
        ))}

      {/* This page onboards credentials; choosing which of these a given
          project actually runs on is per-project, so it lives with the
          project rather than here. */}
      {scope === "project" && allAllowed.length > 0 && (
        <p className="text-muted-foreground -mt-4 text-[12.5px]">
          Choose which of these a project uses on its{" "}
          <span className="text-foreground">Settings → Model</span> tab.
        </p>
      )}

      {/* Org Admin's grid is catalog-seeded (see providerGroups above), so
          this only fires when the catalog itself has nothing in it — a
          near-impossible state in practice, not "nobody has connected a
          provider yet" (which is now the NORMAL starting state for a brand
          new tenant, and renders a full grid of zero-connection cards
          instead). BU scope is ALSO catalog-adjacent now (providerGroups
          above seeds a tile for every provider grant, even a totally
          unconnected one), so this now only fires for a BU with zero
          provider grants at all — nothing to click "Add key" on yet, hence
          no button here for it either (contrast Project scope, whose plain
          "Add provider" stays page-level). */}
      {providerGroups.length === 0 ? (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            {isOrg
              ? "The model catalogue is empty right now, so there's nothing to grant yet."
              : scope === "bu"
                ? scopedUnits.length === 1
                  ? `Your Organization Admin hasn't granted ${scopedUnits[0]!.name} a provider yet.`
                  : `Your Organization Admin hasn't granted your ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} a provider yet.`
                : scopedUnits.length === 1
                  ? `No model provider onboarded in ${scopedUnits[0]!.name} yet.`
                  : `No model provider onboarded in your ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} yet.`}
          </p>
          {scope === "project" && (
            <Button
              onClick={() => setAddOpen(true)}
              disabled={effectiveCatalog.length === 0}
              className="from-brand-gradient-from to-brand-gradient-to mt-5 bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
            >
              <Plus className="size-4" aria-hidden />
              Add provider
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {/* Searches vendors, subscriptions AND the models inside them, so
              "who serves gpt-5.1" is answerable from the list screen rather
              than by opening every card. */}
          <div className="flex items-center justify-between gap-3">
            <div className="border-line-soft bg-surface-1 flex max-w-sm flex-1 items-center gap-2 rounded-lg border px-2.5">
              <Search className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search providers, subscriptions or models…"
                aria-label="Search providers, subscriptions or models"
                className="h-9 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  className="text-muted-foreground hover:text-foreground shrink-0 font-mono text-[10.5px] transition-colors"
                >
                  Clear
                </button>
              )}
            </div>
            {q && (
              <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                {visibleGroups.length} of {providerGroups.length} providers
              </span>
            )}
          </div>

          {visibleGroups.length === 0 ? (
            <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
              <p className="text-muted-foreground text-sm">
                No provider, subscription or model matches &ldquo;{query.trim()}&rdquo;.
              </p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {visibleGroups.map(([kind, group]) => (
                <ProviderCard
                  key={kind}
                  kind={kind}
                  connections={group}
                  spendUsd={spendQ.data ? (spendByProvider.get(kind) ?? 0) : null}
                  isOrg={isOrg}
                  grantedUnitIds={
                    providerGrantsQ.data?.find((g) => g.provider === kind)?.businessUnitIds ?? []
                  }
                  grantableWorkspaces={grantUnits}
                  onToggleGrant={(workspaceId) => toggleProviderGrant(kind, workspaceId)}
                  catalog={catalog}
                  onAddKey={scope === "bu" ? () => setAddKeyProvider(kind) : undefined}
                  grantedModelCount={grantedModelCountByProvider.get(kind) ?? 0}
                />
              ))}
            </div>
          )}
        </div>
      )}

      <AddModelDialog
        open={addOpen || !!addKeyProvider}
        onOpenChange={(v) => {
          if (!v) {
            setAddOpen(false);
            setAddKeyProvider(null);
          }
        }}
        catalog={effectiveCatalog}
        catalogLoading={catalogQ.isLoading || (!isOrg && allowedQueries.some((q) => q.isLoading))}
        targetUnits={isOrg ? null : scopedUnits}
        allowedByUnit={allowedByUnit}
        fullCatalog={catalog}
        needsApproval={addKeyProvider ? false : needsApproval}
        grantableWorkspaces={isOrg ? grantableWorkspaces : null}
        initialProvider={addKeyProvider}
        mode={addKeyProvider ? "bu-add-key" : "org"}
        onAdded={() => {
          invalidateProviders();
          // Org-wide onboarding writes grants too, and every unit's view of
          // what it may use is derived from those.
          queryClient.invalidateQueries({ queryKey: ["model"] });
        }}
      />

    </div>
  );
}

// ───────── Provider card ─────────

/**
 * The vendor's name, its size, and its bill.
 *
 * THE CARD IS A DOOR, NOT A DASHBOARD. It used to carry a status pill, every
 * enabled model as a radio, the verified-at line and a subscription count on
 * top of these — facts about a thing you had not chosen to look at yet,
 * repeated across every card, so choosing between vendors meant reading a
 * paragraph six times. Those belong one level down, against the model or the
 * key they describe.
 *
 * What stays are the two figures that make the cards comparable to each other,
 * which is the only decision this screen supports: how much of the estate a
 * vendor is, and what it costs this month. Both are per-provider, so neither
 * has a better home than the provider's own card.
 *
 * ONE link, stretched over the card by its ::after — not a click handler on the
 * Card. A div with an onClick is invisible to a keyboard and to a screen
 * reader's link list, and cannot be middle-clicked or opened in a new tab,
 * which is exactly what people do with a grid of things to compare.
 *
 * FOR AN ORG ADMIN THE CARD IS NOT A DOOR. There is no per-provider detail
 * screen for them to fall into — that screen is about a single subscription's
 * key, and the Org Admin's flow here is keyless entirely (see spec §2
 * amendment 5). So the card carries its own two controls directly: the
 * `UnitAccessPicker` grants a business unit reach to the provider at all, and
 * the title itself opens the model-curation dialog, which decides which of
 * that provider's models a granted unit may actually use.
 */
function ProviderCard({
  kind,
  connections,
  spendUsd,
  isOrg,
  grantedUnitIds,
  grantableWorkspaces,
  onToggleGrant,
  catalog,
  onAddKey,
  grantedModelCount,
}: {
  kind: string;
  /** Every subscription onboarded for this provider — zero for a BU Admin's
   *  granted-but-unkeyed tile (see `onAddKey`), at least one otherwise. */
  connections: ModelProvider[];
  /** This month's spend for the provider KIND, or null while it loads. */
  spendUsd: number | null;
  /** Org Admin view: renders the grant-toggle and model-curation controls
   *  instead of the detail-screen link. */
  isOrg: boolean;
  /** Business units currently granted this provider (Org Admin only). */
  grantedUnitIds: string[];
  /** Every grantable business unit — Org Admin only. */
  grantableWorkspaces: { id: string; name: string }[];
  /** Toggles ONE business unit's grant for this provider. */
  onToggleGrant: (workspaceId: string) => void;
  /** The full catalogue, for the model-curation dialog's provider models —
   *  Org Admin only, unused otherwise. */
  catalog: CatalogProvider[];
  /** BU Admin scope only, and only set at all when `connections` is empty:
   *  opens the "Add key" dialog fixed to this provider (spec §5, Task 10).
   *  Undefined for the Org Admin view and for a BU's already-keyed tiles,
   *  which keep the detail-page Link instead. */
  onAddKey?: () => void;
  /** How many models the BU was granted for this provider — the only figure a
   *  zero-connection tile has to show instead of a model/spend count that has
   *  nothing to be counted from yet. Unused once `connections` is non-empty. */
  grantedModelCount?: number;
}) {
  const label = providerLabel(kind);
  const [modelsOpen, setModelsOpen] = React.useState(false);
  const isUnkeyed = !!onAddKey && connections.length === 0;

  /**
   * Models across EVERY subscription, de-duplicated.
   *
   * A model can appear on two keys; counting it twice would claim the provider
   * offers more models than it does — the same double-count the spend figure
   * avoids by being keyed on the provider rather than the connection.
   */
  const modelCount = new Set(
    connections.flatMap((c) => c.offerings.filter((o) => o.enabled).map((o) => o.model_id)),
  ).size;

  return (
    <Card className="border-line-soft bg-panel-elevated focus-within:ring-ring relative flex flex-row items-center gap-3 px-4 py-3.5 shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_4px_14px_-6px_oklch(0_0_0_/_0.35)] transition-shadow focus-within:ring-2 hover:shadow-[0_6px_20px_-8px_oklch(0_0_0_/_0.45)]">
      <ProviderGlyph kind={kind} label={label} />
      <div className="min-w-0 flex-1">
        {isOrg ? (
          <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
            <button
              type="button"
              onClick={() => setModelsOpen(true)}
              title="Curate which models of this provider a granted business unit may use"
              className="hover:text-brand-gradient-from flex min-w-0 items-center gap-1 rounded-sm text-left transition-colors focus-visible:outline-none"
            >
              <span className="truncate">{label}</span>
              <SlidersHorizontal
                className="text-muted-foreground size-3 shrink-0"
                aria-hidden
              />
            </button>
          </h3>
        ) : isUnkeyed ? (
          // No detail page exists yet for zero connections, so this is text,
          // not a link — the stretched ::after overlay below belongs only to
          // the Link case, and the "Add key" button is this tile's one and
          // only interactive element.
          <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
            <span className="block truncate">{label}</span>
          </h3>
        ) : (
          <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
            <Link
              href={`/admin/models/${encodeURIComponent(kind)}`}
              className="block truncate rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
            >
              {label}
            </Link>
          </h3>
        )}
        <p className="text-muted-foreground mt-0.5 font-mono text-[11.5px] tabular-nums">
          {isUnkeyed ? (
            <>
              {grantedModelCount ?? 0} {grantedModelCount === 1 ? "model" : "models"} granted ·
              not yet keyed
            </>
          ) : (
            <>
              {modelCount} {modelCount === 1 ? "model" : "models"}
              {typeof spendUsd === "number" && (
                <>
                  {" · "}
                  <span className="text-foreground font-semibold">
                    {spendUsd.toLocaleString(undefined, {
                      style: "currency",
                      currency: "USD",
                      maximumFractionDigits: 0,
                    })}
                  </span>
                  <span className="ml-1 font-sans">this month</span>
                </>
              )}
            </>
          )}
        </p>
      </div>
      {isOrg ? (
        <UnitAccessPicker
          units={grantableWorkspaces}
          selected={grantedUnitIds}
          onToggle={onToggleGrant}
        />
      ) : isUnkeyed ? (
        <Button
          type="button"
          size="sm"
          onClick={onAddKey}
          className="from-brand-gradient-from to-brand-gradient-to shrink-0 bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
        >
          <Plus className="size-3.5" aria-hidden />
          Add key
        </Button>
      ) : (
        <ChevronRight className="text-muted-foreground size-4 shrink-0" aria-hidden />
      )}

      {isOrg && (
        <ProviderModelCurationDialog
          open={modelsOpen}
          onOpenChange={setModelsOpen}
          kind={kind}
          grantedUnitIds={grantedUnitIds}
          units={grantableWorkspaces}
          catalog={catalog}
        />
      )}
    </Card>
  );
}
