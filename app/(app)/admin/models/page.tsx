"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronsUpDown,
  Clock,
  Loader2,
  Plus,
  ArrowRight,
  Search,
  Trash2,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { PageTitle } from "@/components/app/page-title";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { GrantVisibilityControl } from "@/components/app/grant-visibility-control";
import {
  ModelGovernanceSummary,
  countModelGovernance,
} from "@/components/app/model-governance-summary";
import { ModelAvailabilityCard } from "@/components/app/model-availability-card";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useRawSession } from "@/components/auth/session-provider";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import {
  addModelProvider,
  getBuAllowedModels,
  getModelCatalog,
  getModelGrantMatrix,
  listAllModelProviders,
  listModelProviders,
  setModelDefault,
  verifyModelProvider,
} from "@/lib/api/models";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getSpendSeries } from "@/lib/api/cost";
import { qk } from "@/lib/api/query-keys";
import { providerLabel } from "@/lib/models/provider-labels";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { GrantVisibility } from "@/lib/schemas/grant";
import type {
  CatalogProvider,
  ModelAllowEntry,
  ModelOffering,
  ModelProvider,
  ModelProviderKind,
  ModelProviderStatus,
} from "@/lib/schemas/model";

/**
 * Providers with no vendor-wide endpoint — the URL is part of the credential,
 * not an override of it.
 *
 * Azure gives every resource its own hostname, Bedrock and Vertex route by
 * region, and none of the three can be called without one. Saving such a
 * connection with a blank base produces a credential that cannot be used and
 * cannot say why, so these are required rather than merely hinted.
 *
 * A table of known cases, not a rule: an unlisted provider stays optional,
 * because guessing that some new slug needs an endpoint would block onboarding
 * on our ignorance rather than on the provider's requirements.
 */
const ENDPOINT_REQUIRED: Record<string, { placeholder: string; why: string }> = {
  azure: {
    placeholder: "https://<resource>.openai.azure.com/openai/deployments/<deployment>",
    why: "Azure has no shared endpoint — every resource has its own, so the deployment URL is part of this credential.",
  },
  bedrock: {
    placeholder: "https://bedrock-runtime.<region>.amazonaws.com",
    why: "The region endpoint decides where inference runs, and therefore where the prompts go.",
  },
  vertex_ai: {
    placeholder: "https://<location>-aiplatform.googleapis.com",
    why: "Vertex routes by location, so the endpoint is what pins this credential to a region.",
  },
};

/**
 * One numbered step of the onboarding form.
 *
 * The form discloses in order — provider, then models, then the credential —
 * because each answer decides what the next one may contain, and asking for an
 * API key first asks for the hardest value before anything explains which one.
 * Numbering makes the reveal read as progress rather than as fields appearing
 * from nowhere.
 */
function Step({
  n,
  title,
  hint,
  aside,
  children,
}: {
  n: number;
  title: string;
  hint?: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex items-start gap-2.5">
        <span
          aria-hidden
          className="border-line-soft bg-surface-2 text-muted-foreground mt-px grid size-5 shrink-0 place-items-center rounded-full border font-mono text-[10.5px]"
        >
          {n}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <div className="text-[13px] font-medium">{title}</div>
            {aside}
          </div>
          {hint && <p className="text-muted-foreground mt-0.5 text-[11.5px]">{hint}</p>}
        </div>
      </div>
      <div className="space-y-2.5 pl-[30px]">{children}</div>
    </section>
  );
}

function ProviderGlyph({ label }: { label: string }) {
  return (
    <div
      aria-hidden
      className="border-line-soft bg-surface-2 text-muted-foreground grid size-10 shrink-0 place-items-center rounded-lg border font-mono text-base font-semibold"
    >
      {label.charAt(0)}
    </div>
  );
}

/** Keep only the catalog entries a grant actually permits — the cascade's
 *  enforcement point in the UI: a BU Admin's and a Project Admin's onboarding
 *  dialog can only offer models the Org Admin granted their unit. */
function filterCatalogToAllowed(
  catalog: CatalogProvider[],
  allowed: ModelAllowEntry[],
): CatalogProvider[] {
  const allowedSet = new Set(allowed.map((e) => `${e.provider}::${e.model_id}`));
  return catalog
    .map((c) => ({
      ...c,
      models: c.models.filter((m) => allowedSet.has(`${c.provider}::${m.model_id}`)),
    }))
    .filter((c) => c.models.length > 0);
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

  // No edit/remove/verify state here any more. Those act on a single key and
  // now live on the provider's own screen, which is the only place that also
  // shows what each key serves.
  const [addOpen, setAddOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");

  // Prefix-invalidate: with one query per unit there is no single key to name,
  // and a provider added to one unit still changes this page's totals.
  const invalidateProviders = () =>
    queryClient.invalidateQueries({ queryKey: ["model", "providers"] });

  const setDefaultMutation = useMutation({
    mutationFn: (offeringId: string) => setModelDefault(offeringId),
    onSuccess: () => {
      toast.success("Default model updated");
      invalidateProviders();
    },
    onError: (err) =>
      toast.error("Couldn't set default model", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

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

  /**
   * One card per PROVIDER, not per connection.
   *
   * A subscription is a credential, not a provider. Rendering a card each gave
   * two "Anthropic" tiles that both claimed the provider's whole spend — the
   * figure is keyed by provider, so splitting the card double-counted it — and
   * split one vendor's models across two places for no reason a reader could
   * see. The subscriptions live on the detail screen, which already lists them
   * and is where testing, editing and removing a key belongs.
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
    return [...by.entries()];
  })();

  /**
   * Search across all three things a provider card carries: the vendor, the
   * subscriptions under it, and the models those cover.
   *
   * Matching the vendor name alone would answer the question nobody has — you
   * can already see the vendors. The questions that bring you here are "which
   * contract is the EU one" and "who serves gpt-5.1", and the second is only
   * answerable by searching the models inside the cards.
   */
  const q = query.trim().toLowerCase();
  const visibleGroups = q
    ? providerGroups.filter(
        ([kind, group]) =>
          providerLabel(kind).toLowerCase().includes(q) ||
          kind.toLowerCase().includes(q) ||
          group.some(
            (p) =>
              p.display_name.toLowerCase().includes(q) ||
              p.offerings.some((o) => o.model_id.toLowerCase().includes(q)),
          ),
      )
    : providerGroups;

  const catalog = catalogQ.data ?? [];
  const unitNameById = new Map(scopedUnits.map((u) => [u.id, u.name] as const));

  // Everything the viewer could credential anywhere they're bound — the union
  // across their units. The dialog narrows this again to the ONE unit being
  // onboarded into, which is the set that actually governs the save.
  const allAllowed = Object.values(allowedByUnit).flat();

  // The onboarding dialog's provider picker is scoped by the cascade: an Org
  // Admin sees the full catalog (they define what's permitted at all), while a
  // BU or Project Admin sees only what the Org Admin granted their units.
  const effectiveCatalog = isOrg ? catalog : filterCatalogToAllowed(catalog, allAllowed);

  // The single org-wide default across every provider's enabled offerings —
  // only Org Admin's own org-wide providers participate in this.
  const defaultOfferingId = providers
    .flatMap((p) => p.offerings)
    .find((o) => o.is_default)?.id;

  const HEADER_COPY: Record<"org" | "bu" | "project", { eyebrow: string; title: string; body: string }> = {
    org: {
      eyebrow: "Govern",
      title: "Models",
      body: "Approve the models this organization may use, decide which business units get each one, and bring your own LLM keys. Agent runs use these models — and only these.",
    },
    bu: {
      eyebrow: "Govern",
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

        <Button
          onClick={() => setAddOpen(true)}
          disabled={effectiveCatalog.length === 0}
          title={effectiveCatalog.length === 0 ? "No allowed models to onboard from yet" : undefined}
          className="from-brand-gradient-from to-brand-gradient-to shrink-0 bg-gradient-to-br font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.65)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.8)]"
        >
          <Plus className="size-4" aria-hidden />
          Add provider
        </Button>
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

      {providers.length === 0 ? (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            {isOrg
              ? "No model provider configured yet. Agent runs are blocked until an admin adds and verifies one."
              : scopedUnits.length === 1
                ? `No model provider onboarded in ${scopedUnits[0]!.name} yet.`
                : `No model provider onboarded in your ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} yet.`}
          </p>
          <Button
            onClick={() => setAddOpen(true)}
            disabled={effectiveCatalog.length === 0}
            className="from-brand-gradient-from to-brand-gradient-to mt-5 bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
          >
            <Plus className="size-4" aria-hidden />
            Add provider
          </Button>
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
        <RadioGroup
          value={isOrg ? (defaultOfferingId ?? "") : ""}
          onValueChange={(v) => isOrg && setDefaultMutation.mutate(v)}
          className="grid gap-3 sm:grid-cols-2"
          aria-label={isOrg ? "Org-wide default model" : "Enabled models"}
        >
          {visibleGroups.map(([kind, group]) => (
            <ProviderCard
              key={kind}
              connections={group}
              radioDisabled={!isOrg}
              // Only worth naming when there's more than one unit in view;
              // otherwise it repeats the card above on every tile.
              unitName={
                scopedUnits.length > 1 && group[0]?.workspaceId
                  ? (unitNameById.get(String(group[0].workspaceId)) ?? null)
                  : null
              }
              spendUsd={spendQ.data ? (spendByProvider.get(kind) ?? 0) : null}
            />
          ))}
        </RadioGroup>
          )}
        </div>
      )}

      <AddProviderDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        catalog={effectiveCatalog}
        catalogLoading={catalogQ.isLoading || (!isOrg && allowedQueries.some((q) => q.isLoading))}
        targetUnits={isOrg ? null : scopedUnits}
        allowedByUnit={allowedByUnit}
        fullCatalog={catalog}
        needsApproval={needsApproval}
        grantableWorkspaces={isOrg ? grantableWorkspaces : null}
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

function ProviderCard({
  connections,
  radioDisabled,
  unitName,
  spendUsd,
}: {
  /** Every subscription onboarded for this provider — at least one. */
  connections: ModelProvider[];
  /** True outside org scope — no scoped provider participates in the single
   *  org-wide default, so the radio is shown inert rather than interactive. */
  radioDisabled?: boolean;
  /** Which Business Unit this connection lives in. Passed only when the viewer
   *  is looking at more than one, since otherwise every card would repeat the
   *  same name the section above already gives. */
  unitName?: string | null;
  /** This month's spend for the provider KIND, or null while it loads. */
  spendUsd?: number | null;
}) {
  const provider = connections[0]!;
  const label = providerLabel(provider.provider);
  const multi = connections.length > 1;

  /**
   * Models across EVERY subscription, de-duplicated.
   *
   * A model can appear on two keys; listing it twice would suggest two models.
   * Which key serves which is the detail screen's job — this card only claims
   * the provider offers it.
   */
  const enabledOfferings = (() => {
    const seen = new Set<string>();
    return connections
      .flatMap((c) => c.offerings.filter((o) => o.enabled))
      .filter((o) => (seen.has(o.model_id) ? false : (seen.add(o.model_id), true)));
  })();

  // Worst status wins: one broken key means the provider is not wholly healthy,
  // and a green card over a dead subscription is the wrong reassurance.
  const worst =
    connections.find((c) => c.status === "invalid") ??
    connections.find((c) => c.status !== "valid") ??
    provider;
  const pending = connections.some((c) => c.approvalStatus === "pending_approval");
  const rejected = provider.approvalStatus === "rejected";

  return (
    /* The whole card is the drill-in.
       `overflow-hidden` is gone deliberately: it clips the stretched link's
       ::after overlay, which is what makes the card clickable. */
    <Card className="border-line-soft bg-panel-elevated relative flex flex-col shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_4px_14px_-6px_oklch(0_0_0_/_0.35)] transition-shadow hover:shadow-[0_6px_20px_-8px_oklch(0_0_0_/_0.45)] focus-within:ring-ring focus-within:ring-2">
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <ProviderGlyph label={label} />
          <div className="min-w-0 flex-1">
            {/* The PROVIDER names the card, not a subscription. With the
                connections merged, borrowing the first one's display name
                titled the whole card after one of the several contracts
                listed inside it.

                ONE link, stretched over the card by its ::after — not a click
                handler on the Card. A div with an onClick is invisible to a
                keyboard and to a screen reader's link list, and cannot be
                middle-clicked or opened in a new tab, which is exactly what
                people do with a grid of things to compare. */}
            <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
              <Link
                href={`/admin/models/${encodeURIComponent(provider.provider)}`}
                className="rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
              >
                {label}
              </Link>
            </h3>
            <p className="text-muted-foreground mt-0.5 truncate text-[12px]">
              {multi
                ? `${connections.length} subscriptions`
                : provider.display_name}
              {unitName && (
                <>
                  {" · "}
                  <span className="font-mono text-[11px]">{unitName}</span>
                </>
              )}
            </p>
            {/* The provider's own spend, on the provider's own card. The
                figure that used to sit in a chart above the list, next to the
                thing it describes. */}
            {typeof spendUsd === "number" && (
              <p className="mt-1 font-mono text-[12.5px] font-semibold tabular-nums">
                {spendUsd.toLocaleString(undefined, {
                  style: "currency",
                  currency: "USD",
                  maximumFractionDigits: 0,
                })}
                <span className="text-muted-foreground ml-1 font-sans text-[11px] font-normal">
                  this month
                </span>
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <StatusPill status={worst.status} />
            {pending && (
              <span className="text-warning bg-warning/10 border-warning/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider">
                <Clock className="size-2.5" aria-hidden />
                Pending BU approval
              </span>
            )}
            {rejected && (
              <span className="text-destructive bg-destructive/10 border-destructive/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider">
                <XCircle className="size-2.5" aria-hidden />
                Rejected
              </span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-4 pt-0">
        {enabledOfferings.length === 0 ? (
          <p className="text-muted-foreground text-[12px]">No models enabled for this provider.</p>
        ) : (
          /* Lifted above the stretched link. These radios choose the one
             org-wide default model, and the card-wide overlay would otherwise
             swallow the click and navigate instead of selecting. */
          <ul className="relative z-10 space-y-1.5">
            {enabledOfferings.map((o) => (
              <OfferingRow key={o.id} offering={o} radioDisabled={radioDisabled} />
            ))}
          </ul>
        )}

        <p className="text-muted-foreground font-mono text-[11px]">
          {rejected && provider.approvalReason
            ? `Rejected: "${provider.approvalReason}"`
            : provider.last_verified_at
              ? `Verified ${formatDistanceToNow(new Date(provider.last_verified_at))} ago`
              : "Not verified yet"}
        </p>

        {/* Where the subscription actions used to be. Testing, editing and
            removing act on a KEY, and a provider can hold several — so the card
            carried up to three buttons per contract, and the list stopped being
            a list of providers. They now live on the provider's own screen,
            beside the models each key actually serves, which is the context you
            need to decide whether removing one is safe. */}
        <p className="text-muted-foreground mt-auto inline-flex items-center gap-1 font-mono text-[11px]">
          {multi ? `${connections.length} subscriptions` : "1 subscription"}
          <ArrowRight className="size-3" aria-hidden />
        </p>
      </CardContent>
    </Card>
  );
}

// ───────── Offering row — radio chooses the single org-wide default ─────────

function OfferingRow({ offering, radioDisabled }: { offering: ModelOffering; radioDisabled?: boolean }) {
  const radioId = `default-${offering.id}`;
  return (
    <li className="flex items-center gap-2.5">
      <RadioGroupItem
        value={offering.id}
        id={radioId}
        disabled={radioDisabled}
        aria-label={`Make ${offering.model_id} the default model`}
      />
      <Label
        htmlFor={radioId}
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 font-normal"
      >
        <span className="truncate font-mono text-[12px]">{offering.model_id}</span>
        {offering.is_default && (
          <span className="text-success bg-success/10 border-success/30 shrink-0 rounded-full border px-1.5 py-0.5 font-mono text-[10px] font-semibold">
            default
          </span>
        )}
        {(offering.rpm_limit != null ||
          offering.tpm_limit != null ||
          offering.cost_limit_usd != null) && (
          <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
            {[
              offering.rpm_limit != null ? `${offering.rpm_limit} rpm` : null,
              offering.tpm_limit != null ? `${offering.tpm_limit} tpm` : null,
              offering.cost_limit_usd != null ? `$${offering.cost_limit_usd}/mo` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        )}
      </Label>
    </li>
  );
}

// ───────── Status pill — ModelProviderStatus tones via tokens ─────────

function StatusPill({ status }: { status: ModelProviderStatus }) {
  const tone =
    status === "valid"
      ? "text-success bg-success/10 border-success/30"
      : status === "unverified"
        ? "text-warning bg-warning/15 border-warning/40"
        : "text-destructive bg-destructive/10 border-destructive/30";
  const Icon: LucideIcon =
    status === "valid" ? CheckCircle2 : status === "unverified" ? Clock : AlertTriangle;
  const text = status === "valid" ? "Valid" : status === "unverified" ? "Unverified" : "Invalid";
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold",
        tone,
      )}
    >
      <Icon className="size-3" aria-hidden />
      {text}
    </span>
  );
}

// ───────── Add provider dialog ─────────

function AddProviderDialog({
  open,
  onOpenChange,
  catalog,
  catalogLoading,
  targetUnits,
  allowedByUnit,
  fullCatalog,
  needsApproval,
  grantableWorkspaces,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The union of what the viewer may credential — used until they pick a
   *  unit, after which `allowedByUnit` narrows it to that one. */
  catalog: CatalogProvider[];
  catalogLoading: boolean;
  /** null = org-wide onboarding (Org Admin). Otherwise the units the viewer is
   *  bound to: a credential belongs to exactly one, so when there are several
   *  the choice is made HERE rather than inherited from the chrome. */
  targetUnits: { id: string; name: string }[] | null;
  /** Per-unit grants — what each unit may actually be credentialed with. */
  allowedByUnit: Record<string, ModelAllowEntry[]>;
  /** The unfiltered catalog, re-narrowed once a target unit is chosen. */
  fullCatalog: CatalogProvider[];
  /** True for a Project Admin's onboarding — created pending, needs their BU
   *  Admin's approval before it's verified or usable. */
  needsApproval: boolean;
  /** Non-null only for an Org Admin: the units a `specific` grant could name.
   *  A BU or Project Admin is credentialing models that were already granted
   *  to them, so there is no reach to choose and the control is absent. */
  grantableWorkspaces: { id: string; displayName: string }[] | null;
  onAdded: () => void;
}) {
  const [provider, setProvider] = React.useState<ModelProviderKind | "">("");
  const [providerOpen, setProviderOpen] = React.useState(false);
  const [displayName, setDisplayName] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [apiBase, setApiBase] = React.useState("");
  const [enabled, setEnabled] = React.useState<Record<string, boolean>>({});
  const [modelQuery, setModelQuery] = React.useState("");
  const [pending, setPending] = React.useState(false);
  // Escape hatch — models not in LiteLLM's list (self-hosted / brand-new), priced manually.
  const [customModels, setCustomModels] = React.useState<
    { model_id: string; input: string; output: string }[]
  >([]);
  // Optional per-model usage limits, applied to every model added in this provider.
  const [rpmLimit, setRpmLimit] = React.useState("");
  const [tpmLimit, setTpmLimit] = React.useState("");
  const [costLimit, setCostLimit] = React.useState("");
  // Limits are the one section that is genuinely skippable, so it starts shut.
  const [limitsOpen, setLimitsOpen] = React.useState(false);
  // Org-wide onboarding only — how far the models added here reach.
  const [visibility, setVisibility] = React.useState<GrantVisibility>("global");
  const [grantedUnits, setGrantedUnits] = React.useState<string[]>([]);
  // Unit-scoped onboarding only — which unit this credential lands in.
  const [targetUnitId, setTargetUnitId] = React.useState<string>("");

  const resolvedTargetId = targetUnits
    ? (targetUnitId || targetUnits[0]?.id || "")
    : null;

  // Once a unit is chosen the offer narrows to THAT unit's grants — the union
  // is only right until there's a target, and saving against the union would
  // let a model granted to unit A be credentialed into unit B (the server
  // clamps it away, which would read as a save that silently did nothing).
  const activeCatalog = React.useMemo(() => {
    if (!targetUnits || targetUnits.length <= 1) return catalog;
    return filterCatalogToAllowed(fullCatalog, allowedByUnit[resolvedTargetId ?? ""] ?? []);
  }, [targetUnits, catalog, fullCatalog, allowedByUnit, resolvedTargetId]);

  const selectedCatalog = activeCatalog.find((c) => c.provider === provider);
  const providerModels = selectedCatalog?.models ?? [];

  React.useEffect(() => {
    if (!open) {
      setProvider("");
      setProviderOpen(false);
      setDisplayName("");
      setApiKey("");
      setApiBase("");
      setEnabled({});
      setModelQuery("");
      setCustomModels([]);
      setRpmLimit("");
      setTpmLimit("");
      setCostLimit("");
      setLimitsOpen(false);
      setVisibility("global");
      setGrantedUnits([]);
      setTargetUnitId("");
      setPending(false);
    }
  }, [open]);

  // Switching target unit invalidates the model picks: they were chosen from
  // the other unit's grants and may not exist in this one.
  React.useEffect(() => {
    setProvider("");
    setEnabled({});
    setModelQuery("");
  }, [targetUnitId]);

  // Picking a provider just prefills the display name; the admin chooses models.
  const onProviderChange = (slug: string) => {
    setProvider(slug);
    setProviderOpen(false);
    setEnabled({});
    setModelQuery("");
    const label = activeCatalog.find((c) => c.provider === slug)?.label ?? slug;
    if (!displayName.trim()) setDisplayName(label);
  };

  const enabledModels = Object.entries(enabled)
    .filter(([, v]) => v)
    .map(([k]) => k);

  const filteredModels = providerModels.filter((m) =>
    m.model_id.toLowerCase().includes(modelQuery.trim().toLowerCase()),
  );

  const validCustomModels = customModels.filter(
    (m) =>
      m.model_id.trim().length > 0 &&
      m.input.trim() !== "" &&
      m.output.trim() !== "" &&
      Number.isFinite(Number(m.input)) &&
      Number.isFinite(Number(m.output)) &&
      Number(m.input) >= 0 &&
      Number(m.output) >= 0,
  );

  // Which models this credential will carry — and the gate for every step
  // after it, since a credential for no models is not a thing worth naming.
  const chosenModelCount = enabledModels.length + validCustomModels.length;
  const hasModels = chosenModelCount > 0;

  // The KEY IS OPTIONAL. Registering a provider without one is a real choice:
  // its models enter the catalogue and can be granted, and each Business Unit
  // brings its own secret. Demanding a key here made that impossible, so an
  // organisation approving a provider it does not itself pay for had no route.
  const sharedValid = displayName.trim().length > 0;
  const endpoint = provider ? ENDPOINT_REQUIRED[provider] : undefined;
  const endpointValid = !endpoint || apiBase.trim().length > 0;
  // Reach is genuinely optional HERE, and only here: onboarding a provider and
  // deciding who may use it are two decisions, and an admin who holds the key
  // but not yet the answer must still be able to save. Editing an existing
  // grant is the opposite case — emptying it there means "revoke everywhere",
  // which is a thing to warn about rather than to wave through.
  const targetValid = !targetUnits || !!resolvedTargetId;
  const canSubmit = !!provider && hasModels && sharedValid && endpointValid && targetValid;

  // Numbered so the reveal reads as progress. Availability exists only for the
  // Org Admin, so the step after it shifts up rather than leaving a hole.
  const STEP = {
    provider: 1,
    models: 2,
    credential: 3,
    availability: 4,
    limits: grantableWorkspaces ? 5 : 4,
  };
  const limitCount = [rpmLimit, tpmLimit, costLimit].filter((v) => v.trim() !== "").length;

  const updateCustomModel = (i: number, patch: Partial<(typeof customModels)[number]>) =>
    setCustomModels((prev) => prev.map((m, idx) => (idx === i ? { ...m, ...patch } : m)));

  const handleSubmit = async () => {
    if (!canSubmit || pending) return;
    setPending(true);
    try {
      // Catalog picks carry their known pricing; custom rows carry manual pricing.
      // Optional limits are applied uniformly to every model added here.
      const numOrNull = (s: string) => {
        const n = Number(s.trim());
        return s.trim() !== "" && Number.isFinite(n) && n >= 0 ? n : null;
      };
      const limits = {
        rpm_limit: numOrNull(rpmLimit),
        tpm_limit: numOrNull(tpmLimit),
        cost_limit_usd: numOrNull(costLimit),
      };
      const priceOf = (id: string) => providerModels.find((m) => m.model_id === id);
      const models = [
        ...enabledModels.map((id) => ({
          model_id: id,
          input_price_per_million: priceOf(id)?.input_price_per_million ?? null,
          output_price_per_million: priceOf(id)?.output_price_per_million ?? null,
          ...limits,
        })),
        ...validCustomModels.map((m) => ({
          model_id: m.model_id.trim(),
          input_price_per_million: Number(m.input),
          output_price_per_million: Number(m.output),
          ...limits,
        })),
      ];
      const created = await addModelProvider({
        provider,
        display_name: displayName.trim(),
        api_key: apiKey,
        api_base: apiBase.trim() || undefined,
        models,
        workspaceId: resolvedTargetId,
        // Only the Org Admin's onboarding carries a grant; a unit-scoped one
        // is credentialing models that were already granted to it.
        ...(grantableWorkspaces
          ? { visibility, businessUnitIds: visibility === "specific" ? grantedUnits : [] }
          : {}),
      });
      if (needsApproval) {
        toast.info("Sent for approval", {
          description: `A Business Unit Admin needs to approve ${created.display_name} before it's usable.`,
        });
      } else if (!apiKey.trim()) {
        // Nothing to probe. Reporting "verified" over a connection that cannot
        // make a call would be the one lie this dialog must not tell.
        toast.success("Provider registered", {
          description: `${created.display_name} has no key yet — its models can be granted, but stay inert until one is added.`,
        });
      } else {
        const result = await verifyModelProvider(created.id);
        if (result.status === "valid") {
          toast.success("Provider verified ✓");
        } else {
          toast.error("Key rejected — verification failed");
        }
      }
      onAdded();
      onOpenChange(false);
    } catch (err) {
      toast.error("Couldn't add provider", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !pending && onOpenChange(v)}>
      <DialogContent className="max-h-[92vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-display">Add model provider</DialogTitle>
          <DialogDescription>
            {needsApproval
              ? "Pick the provider, then its models, then the key. Any key you give is stored in the tenant's secrets vault and never shown again. Your Business Unit Admin approves before it's live — no live verification runs until then."
              : "Pick the provider, then its models, then the key. Any key you give is stored in the tenant's secrets vault and never shown again, and we run a 1-token live probe to verify it on save."}
          </DialogDescription>
        </DialogHeader>

        {needsApproval && (
          <div className="border-warning/30 bg-warning/10 flex items-start gap-2.5 rounded-xl border px-3.5 py-3">
            <Clock className="text-warning mt-0.5 size-4 shrink-0" aria-hidden />
            <p className="text-[12.5px] leading-relaxed">
              This will be sent to your Business Unit Admin for approval before its models can be used.
            </p>
          </div>
        )}

        <div className="space-y-4">
          {/* Which unit this credential belongs to. Only asked when there is
              genuinely a choice — with one unit it is not a decision, and a
              select with a single option is just noise. */}
          {targetUnits && targetUnits.length > 1 && (
            <div className="space-y-1.5">
              <Label htmlFor="target-unit">{BUSINESS_UNIT_LABEL}</Label>
              <select
                id="target-unit"
                value={resolvedTargetId ?? ""}
                onChange={(e) => setTargetUnitId(e.target.value)}
                disabled={pending}
                className="border-line-soft bg-surface-1 h-9 w-full rounded-md border px-3 text-[13px]"
              >
                {targetUnits.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
              <p className="text-muted-foreground text-[11px]">
                You&apos;re in more than one {BUSINESS_UNIT_LABEL.toLowerCase()}. This key is stored
                for the one you pick, and only its granted models are offered below.
              </p>
            </div>
          )}

          {/* Searchable combobox over LiteLLM's full provider catalog. First
              because every other answer depends on it. */}
          <Step
            n={STEP.provider}
            title="Provider"
            hint="Any provider LiteLLM supports — Anthropic, OpenAI, Azure OpenAI, AWS Bedrock, Google Vertex, xAI. Pick one to see its models with pricing."
          >
            <Popover open={providerOpen} onOpenChange={setProviderOpen}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  aria-expanded={providerOpen}
                  disabled={catalogLoading}
                  className="border-line-soft w-full justify-between font-normal"
                >
                  <span className={cn(!provider && "text-muted-foreground")}>
                    {catalogLoading
                      ? "Loading providers…"
                      : provider
                        ? (selectedCatalog?.label ?? provider)
                        : "Search providers…"}
                  </span>
                  <ChevronsUpDown className="size-4 opacity-50" aria-hidden />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                <Command>
                  <CommandInput placeholder="Search providers…" />
                  <CommandList>
                    <CommandEmpty>No provider found.</CommandEmpty>
                    <CommandGroup>
                      {activeCatalog.map((c) => (
                        <CommandItem
                          key={c.provider}
                          value={`${c.label} ${c.provider}`}
                          onSelect={() => onProviderChange(c.provider)}
                        >
                          <Check
                            className={cn(
                              "size-4",
                              provider === c.provider ? "opacity-100" : "opacity-0",
                            )}
                            aria-hidden
                          />
                          <span className="flex-1">{c.label}</span>
                          <span className="text-muted-foreground font-mono text-[10.5px]">
                            {c.provider}
                          </span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </Step>

          {/* Models for the chosen provider — auto-listed from LiteLLM, searchable,
              pricing prefilled. */}
          {provider && (
            <Step
              n={STEP.models}
              title="Models"
              hint={
                hasModels
                  ? `${chosenModelCount} chosen — this credential will carry ${chosenModelCount === 1 ? "it" : "them"}.`
                  : "Which of this provider's models this subscription covers. A second subscription can cover the others."
              }
              aside={
                <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
                  USD / 1M tokens
                </span>
              }
            >
              {providerModels.length > 0 ? (
                <>
                  <Input
                    value={modelQuery}
                    onChange={(e) => setModelQuery(e.target.value)}
                    placeholder={`Search ${providerModels.length} models…`}
                    autoComplete="off"
                    className="h-9"
                  />
                  <ul className="divide-line-soft border-line-soft max-h-56 divide-y overflow-y-auto rounded-xl border">
                    {filteredModels.length === 0 ? (
                      <li className="text-muted-foreground p-3 text-[12px]">No models match.</li>
                    ) : (
                      filteredModels.map((m) => {
                        const checkboxId = `enable-${m.model_id}`;
                        const priced =
                          typeof m.input_price_per_million === "number" &&
                          typeof m.output_price_per_million === "number";
                        return (
                          <li key={m.model_id} className="flex items-center gap-3 p-2.5">
                            <Checkbox
                              id={checkboxId}
                              checked={enabled[m.model_id] ?? false}
                              onCheckedChange={(v) =>
                                setEnabled((prev) => ({ ...prev, [m.model_id]: v === true }))
                              }
                            />
                            <Label
                              htmlFor={checkboxId}
                              className="flex min-w-0 flex-1 cursor-pointer items-center justify-between gap-2 font-normal"
                            >
                              <span className="truncate font-mono text-[12px]">{m.model_id}</span>
                              <span className="text-muted-foreground shrink-0 font-mono text-[10.5px] tabular-nums">
                                {priced
                                  ? `$${m.input_price_per_million} / $${m.output_price_per_million}`
                                  : "no list price"}
                              </span>
                            </Label>
                          </li>
                        );
                      })
                    )}
                  </ul>
                </>
              ) : (
                <p className="text-muted-foreground text-[11px]">
                  LiteLLM lists no models for this provider — add yours below with pricing.
                </p>
              )}

              {/* Escape hatch: models not in LiteLLM's list (self-hosted / brand-new). */}
              {customModels.length > 0 && (
                <ul className="space-y-2 pt-1">
                  {customModels.map((m, i) => (
                    <li
                      key={i}
                      className="border-line-soft bg-surface-1 grid grid-cols-[1fr_auto] gap-2 rounded-xl border p-2.5"
                    >
                      <Input
                        value={m.model_id}
                        onChange={(e) => updateCustomModel(i, { model_id: e.target.value })}
                        placeholder="custom model id"
                        autoComplete="off"
                        className="col-span-2 font-mono text-[12px]"
                      />
                      <div className="flex items-center gap-2">
                        <Input
                          type="number"
                          min={0}
                          step="0.01"
                          value={m.input}
                          onChange={(e) => updateCustomModel(i, { input: e.target.value })}
                          placeholder="input $"
                          className="w-28 font-mono text-[12px]"
                          aria-label={`Input price for custom model ${i + 1}`}
                        />
                        <Input
                          type="number"
                          min={0}
                          step="0.01"
                          value={m.output}
                          onChange={(e) => updateCustomModel(i, { output: e.target.value })}
                          placeholder="output $"
                          className="w-28 font-mono text-[12px]"
                          aria-label={`Output price for custom model ${i + 1}`}
                        />
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setCustomModels((prev) => prev.filter((_, idx) => idx !== i))}
                        aria-label={`Remove custom model ${i + 1}`}
                        className="justify-self-end"
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() =>
                  setCustomModels((prev) => [...prev, { model_id: "", input: "", output: "" }])
                }
                className="border-line-soft"
              >
                <Plus className="size-4" aria-hidden />
                Add a model not listed
              </Button>
            </Step>
          )}

          {/* The credential itself — revealed once there are models for it to
              carry. Its name comes first because the name is what every other
              screen shows when it answers "which key runs this model". */}
          {hasModels && (
            <Step
              n={STEP.credential}
              title="Credential"
              hint="How the platform authenticates to this provider — and what the rest of the product calls this subscription."
            >
              <div className="space-y-1.5">
                <Label htmlFor="display-name">Subscription name</Label>
                <Input
                  id="display-name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder={`e.g. ${selectedCatalog?.label ?? "Anthropic"} — Payments enterprise`}
                />
                {/* Names the CONTRACT, not the vendor. An organisation can hold
                    several subscriptions with the same provider — a shared platform
                    one for everyday models, a business unit's own for the expensive
                    one — and every screen that says "which key runs this model"
                    shows this string. Three rows all reading "Anthropic" answer
                    that question with nothing. */}
                <p className="text-muted-foreground text-[11.5px]">
                  Name the subscription, not the vendor — you may hold several with the same
                  provider, and this is what every screen shows when it says which key runs a model.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="api-key">
                  API key{" "}
                  <span className="text-muted-foreground/60 font-normal normal-case">
                    (optional)
                  </span>
                </Label>
                <Input
                  id="api-key"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-… — leave blank to let each unit bring its own"
                  autoComplete="off"
                />
                <p className="text-muted-foreground text-[11.5px]">
                  {apiKey.trim().length > 0
                    ? "The platform pays for these models — no business unit needs a key."
                    : "Registers the provider without a key: its models can be granted, but stay inert until a business unit onboards its own."}
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="api-base">
                  API base{" "}
                  <span className="text-muted-foreground/60 font-normal normal-case">
                    {endpoint ? "(required)" : "(optional)"}
                  </span>
                </Label>
                <Input
                  id="api-base"
                  value={apiBase}
                  onChange={(e) => setApiBase(e.target.value)}
                  placeholder={
                    endpoint?.placeholder ??
                    "https://your-gateway.internal/v1 — for self-hosted / gateway"
                  }
                  aria-invalid={!endpointValid}
                  autoComplete="off"
                  className="font-mono"
                />
                {endpoint && (
                  <p
                    className={cn(
                      "text-[11.5px]",
                      endpointValid ? "text-muted-foreground" : "text-warning",
                    )}
                  >
                    {endpoint.why}
                  </p>
                )}
              </div>
            </Step>
          )}

          {/* Who the models onboarded here reach — the grant, written in the
              same act as the key so a credentialed model can't land invisible. */}
          {hasModels && grantableWorkspaces && (
            <Step
              n={STEP.availability}
              title="Availability"
              hint={`Global reaches every ${BUSINESS_UNIT_LABEL.toLowerCase()} and project automatically. Specific reaches only the ones you name — changeable later from Model access.`}
            >
              <GrantVisibilityControl
                idPrefix="add-provider-grant"
                value={{ visibility, businessUnitIds: grantedUnits }}
                workspaces={grantableWorkspaces}
                disabled={pending}
                optional
                onChange={(next) => {
                  setVisibility(next.visibility);
                  setGrantedUnits(next.businessUnitIds);
                }}
              />
            </Step>
          )}

          {/* Limits — the only section that is genuinely skippable, so it is
              the only one that stays shut. Expanded by default it read as four
              more required fields between the admin and the save button. */}
          {hasModels && (
            <Step
              n={STEP.limits}
              title="Usage limits"
              hint="Optional. Applied to every model in this subscription; blank means no limit."
              aside={
                limitCount > 0 ? (
                  <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
                    {limitCount} set
                  </span>
                ) : undefined
              }
            >
              {limitsOpen ? (
                <>
                  <div className="grid grid-cols-3 gap-2">
                    <Input
                      type="number"
                      min={0}
                      step="1"
                      value={rpmLimit}
                      onChange={(e) => setRpmLimit(e.target.value)}
                      placeholder="RPM"
                      aria-label="Requests per minute limit"
                      className="font-mono text-[12px]"
                    />
                    <Input
                      type="number"
                      min={0}
                      step="1"
                      value={tpmLimit}
                      onChange={(e) => setTpmLimit(e.target.value)}
                      placeholder="TPM"
                      aria-label="Tokens per minute limit"
                      className="font-mono text-[12px]"
                    />
                    <Input
                      type="number"
                      min={0}
                      step="0.01"
                      value={costLimit}
                      onChange={(e) => setCostLimit(e.target.value)}
                      placeholder="Cost $/mo"
                      aria-label="Monthly cost limit in USD"
                      className="font-mono text-[12px]"
                    />
                  </div>
                  <p className="text-muted-foreground text-[11.5px]">
                    RPM (requests/min) is enforced live; TPM (tokens/min) and cost ($/month) are
                    recorded. All three are editable later from Edit on this subscription.
                  </p>
                </>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setLimitsOpen(true)}
                  className="border-line-soft"
                >
                  <Plus className="size-4" aria-hidden />
                  Set RPM, TPM or a monthly cap
                </Button>
              )}
            </Step>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!canSubmit || pending}
            aria-busy={pending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
          >
            {pending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            {/* A save with no key runs no probe, so it must not promise a test. */}
            {pending ? (apiKey.trim() ? "Testing…" : "Saving…") : apiKey.trim() ? "Test & Save" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// The remove confirm moved to the provider screen with the action itself. It
// gained something this one could not have: the models that lose their only
// key, named. This list has no idea what a subscription serves.
