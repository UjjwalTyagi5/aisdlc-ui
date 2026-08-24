"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  FolderPlus,
  Globe,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
  substringFilter,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { AddModelDialog } from "@/components/app/add-model-dialog";
import { EditProviderDialog } from "@/components/app/edit-provider-dialog";
import { SpendRankedBars } from "@/components/app/spend-bar-chart";
import { UnitAccessPicker } from "@/components/app/unit-access-picker";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { getSpendSeries } from "@/lib/api/cost";
import {
  assignProviderToProject,
  deleteModelProvider,
  getModelCatalog,
  getModelGrantMatrix,
  getOrgModelGrants,
  listAllModelProviders,
  listModelProviders,
  setModelDefault,
  setOrgModelGrants,
} from "@/lib/api/models";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { providerLabel } from "@/lib/models/provider-labels";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type {
  ModelGrantMatrixRow,
  ModelOffering,
  ModelProvider,
  OrgModelGrant,
} from "@/lib/schemas/model";
import type { Project } from "@/lib/schemas";

const usd = (n: number) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

const keyOf = (provider: string, modelId: string, credentialId?: string | null) =>
  `${provider}::${modelId}::${credentialId ?? ""}`;

/**
 * One provider, gated by role.
 *
 * `useAccessScope`'s `role` is async (a query, not derived from the session
 * synchronously), so it is genuinely null for a beat on every load — that is
 * the still-resolving state, never "unauthenticated", and must render neither
 * branch below rather than flash one and then swap it for the other. Org
 * Admin and Business Unit Admin are the only roles with a real screen here:
 * Org Admin never touches a credential (spec's hard constraint — see
 * `OrgAdminProviderDetail`'s own docs) and gets the org-wide grant view; BU
 * Admin manages the keys their own unit(s) actually hold. Every other
 * resolved role (Project Admin, contributor, …) still gets `RestrictedAccess`
 * — this task only adds the BU Admin branch, it does not open the page
 * further than that.
 */
export default function ProviderDetailPage() {
  const params = useParams<{ provider: string }>();
  const providerKind = decodeURIComponent(params.provider);
  const { role } = useAccessScope();

  if (role === null) {
    return (
      <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }

  if (role === "bu_admin") {
    return <BuAdminProviderDetail providerKind={providerKind} />;
  }

  if (role !== "org_admin") {
    return (
      <RestrictedAccess description="Provider detail is being rebuilt for the new access model and isn't available yet." />
    );
  }

  return <OrgAdminProviderDetail providerKind={providerKind} />;
}

/**
 * One provider, in full — the Organization Admin's view.
 *
 * The list screen answers "which vendors do we run on". This answers everything
 * model-level, as ONE VERTICAL COLUMN — the table of models first, then the
 * selected model's own analytics beneath it. A side-by-side master/detail was
 * tried and put the models in a narrow rail with the chart squeezed beside it;
 * stacking gives both the full width, and the reading order matches the act:
 * scan the table, pick a row, read what it costs.
 *
 * The table carries what is true of a (model, credential) PAIR:
 *
 *   what each MODEL costs this month
 *   which CREDENTIAL serves it — a provider may hold several, each covering
 *     different models, which is how one key can run production models and
 *     another the cheap ones
 *   which BUSINESS UNITS have it, revocable here
 *
 * The panel below carries what is true of the MODEL: its price per token, and
 * six months of spend as bars. That split is why the chart is not a column — a
 * history has no cell width, and a single "this month" figure cannot tell a
 * step change from a steady bill.
 *
 * Org Admin only. The grant matrix names every unit's standing against every
 * model, which is the organisation's whole posture in one screen — and Org
 * Admin never adds a key here (spec's hard constraint), so the Manage column
 * carries no "Add" verb of any kind, only what already exists.
 */
function OrgAdminProviderDetail({ providerKind }: { providerKind: string }) {
  const queryClient = useQueryClient();
  const { units } = useScopedBusinessUnits();

  // Every connection, not only the org-wide ones. A unit that keyed a provider
  // itself is exactly what this screen has to explain — OpenAI is onboarded by
  // Lending alone, and asking only for org-wide connections made that key
  // invisible here while the matrix still named it.
  const providersQ = useQuery({
    queryKey: qk.model.providers("all"),
    queryFn: () => listAllModelProviders(),
  });
  const matrixQ = useQuery({ queryKey: qk.model.grantMatrix(), queryFn: getModelGrantMatrix });
  const grantsQ = useQuery({ queryKey: qk.model.orgGrants(), queryFn: getOrgModelGrants });
  const spendQ = useQuery({
    queryKey: qk.cost.spendSeries("model", "all", 6),
    queryFn: () => getSpendSeries({ groupBy: "model", months: 6 }),
    staleTime: 60_000,
  });
  const catalogQ = useQuery({ queryKey: qk.model.catalog(), queryFn: getModelCatalog });
  const { data: allWorkspaces } = useWorkspaces();
  const grantableWorkspaces = React.useMemo(
    () => (allWorkspaces ?? []).filter((w) => w.status === "active"),
    [allWorkspaces],
  );

  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);

  /** Every credential onboarded for this provider — usually one, sometimes not. */
  const credentials: ModelProvider[] = React.useMemo(
    () => (providersQ.data ?? []).filter((p) => p.provider === providerKind),
    [providersQ.data, providerKind],
  );

  /**
   * The subscription behind each row, by id.
   *
   * The matrix names the credential; only the connection knows its scope and
   * whether it verified. Both used to live in a Credentials card above the
   * table — removing that card would have dropped them, so they move into the
   * Credential column, against the model they actually govern.
   */
  const credentialById = React.useMemo(
    () => new Map(credentials.map((c) => [c.id, c] as const)),
    [credentials],
  );
  const unitNameById = React.useMemo(
    () => new Map(units.map((u) => [u.id, u.name] as const)),
    [units],
  );

  const rows: ModelGrantMatrixRow[] = React.useMemo(
    () => (matrixQ.data?.rows ?? []).filter((r) => r.provider === providerKind),
    [matrixQ.data, providerKind],
  );

  /** Current-month spend per model, from the same series Cost & Budget reads. */
  const spendByModel = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const s of spendQ.data?.series ?? []) {
      m.set(s.id, s.points[s.points.length - 1] ?? 0);
    }
    return m;
  }, [spendQ.data]);

  /**
   * Summed over DISTINCT models, not rows.
   *
   * Spend is recorded per model; a model served by two subscriptions has two
   * rows but one bill. Summing rows counted it twice and inflated the provider
   * total — the exact class of double-count this page exists to avoid.
   */
  const providerSpend = React.useMemo(() => {
    const seen = new Set<string>();
    return rows.reduce((a, r) => {
      if (seen.has(r.model_id)) return a;
      seen.add(r.model_id);
      return a + (spendByModel.get(r.model_id) ?? 0);
    }, 0);
  }, [rows, spendByModel]);

  /**
   * Every model this provider serves, with what it cost this month — the top
   * card's bars.
   *
   * Distinct model ids, not rows: a model on two keys is one bill, and a
   * ranking that listed it twice would let the same money win two places.
   */
  const modelSpendRows = React.useMemo(() => {
    const ids = new Set(rows.map((r) => r.model_id));
    return [...ids].map((id) => ({ id, name: id, value: spendByModel.get(id) ?? 0 }));
  }, [rows, spendByModel]);

  /**
   * The provider's own six-month series, summed from its models.
   *
   * Not fetched separately with `groupBy: "provider"` — that call exists and
   * the list screen uses it, but summing the per-model figures already on hand
   * guarantees the header total and the bars beneath it cannot disagree, which
   * two independent aggregations eventually would.
   */
  const providerHistory = React.useMemo(() => {
    const months = spendQ.data?.months ?? [];
    const ids = new Set(rows.map((r) => r.model_id));
    const mine = (spendQ.data?.series ?? []).filter((s) => ids.has(s.id));
    const points = months.map((_, i) => mine.reduce((a, s) => a + (s.points[i] ?? 0), 0));
    return { months, points, total: points.reduce((a, v) => a + v, 0) };
  }, [rows, spendQ.data]);

  /**
   * Filtering the access table.
   *
   * Matches the model, the credential serving it, and the units that hold it —
   * the three columns. Searching only the model id would leave "what does
   * Payments have here" and "what runs on the EU gateway" unanswerable, and
   * those are access questions, which is what this table is for.
   *
   * The unfiltered `rows` still drive spend and the shared-key check above: a
   * search narrows what you read, never what the totals are computed from.
   */
  const [query, setQuery] = React.useState("");
  const visibleRows = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.model_id.toLowerCase().includes(q) ||
        (r.credentialName ?? "").toLowerCase().includes(q) ||
        r.units.some((u) => u.hasAccess && u.name.toLowerCase().includes(q)),
    );
  }, [rows, query]);

  /** Models served by more than one subscription — their spend is not
   *  attributable to a single key, and the row must say so. */
  const sharedModels = React.useMemo(() => {
    const count = new Map<string, number>();
    for (const r of rows) count.set(r.model_id, (count.get(r.model_id) ?? 0) + 1);
    return new Set([...count.entries()].filter(([, n]) => n > 1).map(([id]) => id));
  }, [rows]);

  /**
   * Which model the panel below is showing.
   *
   * Held as a model id, not a row: a model on two keys is two rows and one
   * bill, so selecting either must open the same history. Null means "not
   * chosen yet", which resolves to the first visible row — a panel that stayed
   * blank until you clicked would hide the new part of the page behind a step
   * nothing asks you to take.
   */
  /**
   * The offering behind each model, from whichever key carries it.
   *
   * The matrix row says whether a model is granted and to whom; only the
   * offering knows whether it is the org-wide default. That is the one fact the
   * table needs from outside the matrix, so it is resolved once here rather
   * than searched per row.
   */
  const offeringByModel = React.useMemo(() => {
    const m = new Map<string, ModelOffering>();
    for (const c of credentials) {
      for (const o of c.offerings) if (!m.has(o.model_id)) m.set(o.model_id, o);
    }
    return m;
  }, [credentials]);

  /**
   * The subscription being edited.
   *
   * Rotating a key was reachable only from the Models list card, which is the
   * wrong place to be standing: you discover a key is wrong while looking at
   * the model that runs on it, and that is this screen. The dialog is the same
   * one the list uses — a second edit form would be a second set of rules about
   * what an empty key field means.
   */
  const [editing, setEditing] = React.useState<ModelProvider | null>(null);
  const [removing, setRemoving] = React.useState<ModelProvider | null>(null);
  const [addOpen, setAddOpen] = React.useState(false);

  const invalidateProviders = () =>
    queryClient.invalidateQueries({ queryKey: ["model"] });

  const removeM = useMutation({
    mutationFn: (id: string) => deleteModelProvider(id),
    onSuccess: () => {
      toast.success("Subscription removed");
      setRemoving(null);
      invalidateProviders();
    },
    onError: (err) =>
      toast.error("Couldn't remove subscription", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const saveM = useMutation({
    mutationFn: (entries: OrgModelGrant[]) => setOrgModelGrants(entries),
    onSuccess: () => {
      toast.success("Model access updated");
      queryClient.invalidateQueries({ queryKey: ["model"] });
    },
    onError: (err) =>
      toast.error("Couldn't update model access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  /**
   * The one org-wide default model.
   *
   * It used to be a radio group across the provider cards, which put a choice
   * that is singular for the whole organisation into a grid where nothing said
   * so — six cards each showing their own radios read as six independent
   * settings. It belongs against one model, on the screen that says everything
   * else about that model.
   */
  const defaultM = useMutation({
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

  /**
   * Add or remove ONE unit's access.
   *
   * Creates the grant when there isn't one. Previously this only mapped over
   * existing grants, so an ungranted model silently ignored every click — and
   * since the picker was also disabled in that state, there was no way to give
   * a model to a Business Unit from this screen at all. You could take access
   * away and never give it.
   *
   * The created grant is `specific`: naming a unit is the narrow ask, and
   * turning it into an org-wide grant because someone ticked one box would
   * hand the model to every other unit as a side effect.
   */
  function toggleCell(row: ModelGrantMatrixRow, unitId: string) {
    const k = keyOf(row.provider, row.model_id, row.credentialId);
    const existing = grants.find((g) => keyOf(g.provider, g.model_id, g.credentialId) === k);

    if (!existing) {
      saveM.mutate([
        ...grants,
        {
          provider: row.provider,
          model_id: row.model_id,
          credentialId: row.credentialId,
          visibility: "specific",
          businessUnitIds: [unitId],
        } as OrgModelGrant,
      ]);
      return;
    }

    saveM.mutate(
      grants.flatMap((g) => {
        if (keyOf(g.provider, g.model_id, g.credentialId) !== k) return [g];
        const has = g.businessUnitIds.includes(unitId);
        const next = has
          ? g.businessUnitIds.filter((id) => id !== unitId)
          : [...g.businessUnitIds, unitId];
        // Un-ticking the last unit of a `specific` grant leaves a grant that
        // reaches nobody. That is not "granted to none", it is not granted —
        // and leaving the husk would show the model as granted and unusable.
        if (g.visibility === "specific" && next.length === 0) return [];
        return [{ ...g, businessUnitIds: next }];
      }),
    );
  }

  /** Give this subscription's model to every unit at once. The picker covers
   *  the narrow case; this is the one it cannot express. */
  function grantToAll(row: ModelGrantMatrixRow) {
    const k = keyOf(row.provider, row.model_id, row.credentialId);
    const without = grants.filter((g) => keyOf(g.provider, g.model_id, g.credentialId) !== k);
    saveM.mutate([
      ...without,
      {
        provider: row.provider,
        model_id: row.model_id,
        credentialId: row.credentialId,
        visibility: "global",
        businessUnitIds: [],
      } as OrgModelGrant,
    ]);
  }

  /** Revokes THIS subscription's grant, not the model everywhere — the other
   *  key's grant is a separate decision and survives. */
  function revokeModel(row: ModelGrantMatrixRow) {
    const k = keyOf(row.provider, row.model_id, row.credentialId);
    saveM.mutate(grants.filter((g) => keyOf(g.provider, g.model_id, g.credentialId) !== k));
  }

  const loading = providersQ.isLoading || matrixQ.isLoading || grantsQ.isLoading;
  const label = providerLabel(providerKind);

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <div>
        <Link
          href="/admin/models"
          className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1 font-mono text-[11px] transition-colors"
        >
          <ArrowLeft className="size-3" aria-hidden />
          All providers
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-[32px] leading-[1.05] font-bold tracking-[-0.03em]">
              {label}
            </h1>
            <p className="text-muted-foreground mt-1 text-[13px]">
              What this provider costs, which credential serves each model, and which{" "}
              {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} may use it.
            </p>
          </div>
          <div className="flex items-end gap-5">
            {spendQ.data && (
              <div className="text-right">
                <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                  This month
                </p>
                <p className="font-display text-[26px] leading-none font-bold tabular-nums">
                  {usd(providerSpend)}
                </p>
              </div>
            )}
            {/* The vendor is not asked for again — this button knows which
                provider it is standing on, and the dialog opens with it
                already chosen. */}
            <Button
              onClick={() => setAddOpen(true)}
              className="from-brand-gradient-from to-brand-gradient-to shrink-0 bg-gradient-to-br font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.65)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.8)]"
            >
              <Plus className="size-4" aria-hidden />
              Add model
            </Button>
          </div>
        </div>
      </div>

      {matrixQ.isError ? (
        <ErrorState title="Couldn't load this provider" onRetry={() => matrixQ.refetch()} />
      ) : loading ? (
        <LoadingState variant="card" />
      ) : (
        <>
          {/* ── Cost first: every model, side by side ───────────────────────
              At the TOP because "what is this provider costing us, and which
              model is doing it" is the question you arrive with; the access
              table below is what you do about it. Horizontal bars rather than
              the stacked monthly chart: this compares models to each other, and
              a horizontal form scales to as many as the provider has, where the
              grouped chart folds everything past the third into "Other". */}
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                  Cost by model
                </h2>
                <span className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                  This month
                </span>
              </div>
              <p className="text-muted-foreground text-[12px]">
                All {modelSpendRows.length} {label} models, ranked by what they cost this month.
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* The provider's totals as one horizontal strip — the context
                  the bars are a share of. */}
              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                <Stat label="This month" value={spendQ.data ? usd(providerSpend) : "—"} />
                <Stat
                  label={`Last ${providerHistory.points.length || 6} months`}
                  value={spendQ.data ? usd(providerHistory.total) : "—"}
                />
                <Stat
                  label="Monthly average"
                  value={
                    spendQ.data && providerHistory.points.length > 0
                      ? usd(providerHistory.total / providerHistory.points.length)
                      : "—"
                  }
                />
                <Stat label="Models" value={String(modelSpendRows.length)} />
              </div>

              <SpendRankedBars
                rows={modelSpendRows}
                emptyLabel={`No ${label} spend this month.`}
              />
            </CardContent>
          </Card>

          {/* ── Models: cost, credential, access ────────────────────────────
              One card, not two. A separate Credentials list restated what the
              Credential column already says — the same subscription names, read
              twice — and its own facts (scope, status, keylessness) belong
              against the model they affect rather than in a list you have to
              hold in your head while reading the table. The key's own actions
              live in the row for the same reason. */}
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                Models &amp; access
              </h2>
              <p className="text-muted-foreground text-[12px]">
                Grant a model to every {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")}{" "}
                or to named ones, and manage the key serving it — all on the row it affects.
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              {rows.length === 0 ? (
                <p className="text-muted-foreground text-[12.5px]">
                  No models from {label} are in the catalogue yet.
                </p>
              ) : (
                <>
                  {/* Searches model, credential and unit — the same three
                      columns the table shows, so anything visible is findable. */}
                  <div className="flex items-center justify-between gap-3">
                    <div className="border-line-soft bg-surface-1 flex max-w-sm flex-1 items-center gap-2 rounded-lg border px-2.5">
                      <Search className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                      <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder={`Search ${rows.length} models, credentials or ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}…`}
                        aria-label={`Search models, credentials or ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`}
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
                    {query.trim() && (
                      <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                        {visibleRows.length} of {rows.length}
                      </span>
                    )}
                  </div>

                  {visibleRows.length === 0 ? (
                    <p className="text-muted-foreground py-4 text-center text-[12.5px]">
                      No model, credential or {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")} matches &ldquo;{query.trim()}&rdquo;.
                    </p>
                  ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] border-collapse text-left">
                    <thead>
                      <tr className="border-line-soft border-b">
                        <th className="text-muted-foreground py-2 pr-3 font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Model
                        </th>
                        <th className="text-muted-foreground px-2 py-2 text-right font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Spend
                        </th>
                        <th className="text-muted-foreground px-2 py-2 font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Credential
                        </th>
                        <th className="text-muted-foreground px-2 py-2 font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Access
                        </th>
                        <th className="text-muted-foreground px-2 py-2 text-right font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Manage
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-line-soft divide-y">
                      {visibleRows.map((row) => {
                        const isGlobal = row.visibility === "global";
                        // The subscription serving this row, when one does. A
                        // catalogue model nobody has onboarded has no key to
                        // edit or remove, so those actions are absent rather
                        // than disabled.
                        const credential = row.credentialId
                          ? (credentialById.get(row.credentialId) ?? null)
                          : null;
                        const offering = offeringByModel.get(row.model_id) ?? null;
                        return (
                          <tr key={`${row.model_id}::${row.credentialId ?? ""}`}>
                            <td className="py-2.5 pr-3">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-mono text-[12px]">{row.model_id}</span>
                                {/* The org-wide default, marked where the model
                                    is. It used to be a radio on the provider
                                    cards, which showed one singular setting as
                                    six independent ones. */}
                                {offering?.is_default && (
                                  <span className="text-success bg-success/10 border-success/30 shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] font-semibold tracking-wide uppercase">
                                    Default
                                  </span>
                                )}
                                {!row.granted && (
                                  <span className="text-muted-foreground border-line-soft shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                                    Not granted
                                  </span>
                                )}
                                {isGlobal && (
                                  <span className="text-muted-foreground border-line-soft shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                                    Global
                                  </span>
                                )}
                              </div>
                            </td>

                            <td className="px-2 py-2.5 text-right font-mono text-[12px] tabular-nums">
                              {spendQ.data ? usd(spendByModel.get(row.model_id) ?? 0) : "—"}
                              {sharedModels.has(row.model_id) && (
                                // The figure is the MODEL's, not this key's —
                                // usage is not recorded per subscription, and
                                // printing it unqualified on two rows would
                                // read as twice the money.
                                <span className="text-muted-foreground ml-1 block font-sans text-[10px] font-normal">
                                  across all keys
                                </span>
                              )}
                            </td>

                            <td className="px-2 py-2.5">
                              {/* A named subscription is not proof of a usable
                                  one: a provider may be registered without a
                                  key, and a key that exists may never have
                                  verified. Naming it and stopping there would
                                  put a credential beside a model nobody can
                                  call. Each marker below is an exception —
                                  a keyed, verified, platform-owned key shows
                                  its name alone, which is the common case. */}
                              {row.credentialName ? (
                                (() => {
                                  const c = row.credentialId
                                    ? credentialById.get(row.credentialId)
                                    : undefined;
                                  return (
                                    <div className="space-y-0.5">
                                      {/* Plain text. The name used to double as
                                          the Edit control, which put two ways to
                                          reach one dialog on the same row — the
                                          Manage column now carries it as a named
                                          action, and a hidden second trigger is
                                          only discoverable by hovering. */}
                                      <span className="block text-[11.5px]">
                                        {row.credentialName}
                                      </span>
                                      <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                                        {row.credentialHasKey === false && (
                                          <span className="text-warning inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase">
                                            <KeyRound className="size-2.5" aria-hidden />
                                            Holds no key
                                          </span>
                                        )}
                                        {c && c.hasKey && c.status !== "valid" && (
                                          <span className="text-muted-foreground font-mono text-[9.5px] tracking-wide uppercase">
                                            {c.status}
                                          </span>
                                        )}
                                        {/* Whose key it is. Platform-owned is the
                                            default and goes unmarked; a unit's own
                                            key is the thing worth saying, because
                                            only that unit can run on it. */}
                                        {c?.workspaceId && (
                                          <span className="text-muted-foreground font-mono text-[9.5px] tracking-wide uppercase">
                                            {unitNameById.get(String(c.workspaceId)) ??
                                              "Unit-scoped"}{" "}
                                            only
                                          </span>
                                        )}
                                      </span>
                                    </div>
                                  );
                                })()
                              ) : (
                                <span className="text-warning inline-flex items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                                  <KeyRound className="size-2.5" aria-hidden />
                                  No key
                                </span>
                              )}
                            </td>

                            <td className="px-2 py-2.5">
                              <UnitAccessPicker
                                units={units.map((u) => ({ id: u.id, name: u.name }))}
                                selected={row.units.filter((x) => x.hasAccess).map((x) => x.id)}
                                isGlobal={isGlobal}
                                // Enabled even when ungranted: picking a unit
                                // is how you GIVE access, and disabling it left
                                // revoking as the only thing the column could do.
                                disabled={saveM.isPending}
                                onToggle={(unitId) => toggleCell(row, unitId)}
                              />
                            </td>

                            {/* One cell, three verbs, all acting on this row:
                                who may use the model, and the key behind it.
                                Edit and Remove act on the SUBSCRIPTION named in
                                the Credential column — which is why they sit
                                here rather than in a list of their own that
                                repeated those same names. */}
                            <td className="py-2.5 pl-2">
                              <div className="flex items-center justify-end gap-1">
                                {/* Offered only where it is a change: a model
                                    with no offering has nothing to promote, and
                                    one already default has nowhere to go. */}
                                {offering && !offering.is_default && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-muted-foreground hover:text-foreground h-7 px-2 text-[11px]"
                                    disabled={defaultM.isPending}
                                    onClick={() => defaultM.mutate(offering.id)}
                                    title={`Make ${row.model_id} the org-wide default model`}
                                  >
                                    {defaultM.isPending ? (
                                      <Loader2 className="size-3 animate-spin" aria-hidden />
                                    ) : (
                                      "Make default"
                                    )}
                                  </Button>
                                )}

                                {row.granted ? (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-muted-foreground hover:text-destructive h-7 px-2 text-[11px]"
                                    disabled={saveM.isPending}
                                    onClick={() => revokeModel(row)}
                                  >
                                    {saveM.isPending ? (
                                      <Loader2 className="size-3 animate-spin" aria-hidden />
                                    ) : (
                                      "Revoke"
                                    )}
                                  </Button>
                                ) : (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-muted-foreground hover:text-foreground h-7 px-2 text-[11px]"
                                    disabled={saveM.isPending}
                                    onClick={() => grantToAll(row)}
                                  >
                                    {saveM.isPending ? (
                                      <Loader2 className="size-3 animate-spin" aria-hidden />
                                    ) : (
                                      `Grant to all`
                                    )}
                                  </Button>
                                )}

                                {/* No Test action. The row already states the
                                    key's standing — "Holds no key", an
                                    unverified marker, and the provider's status
                                    pill on the list — and a button that only
                                    re-reports what is already on screen is a
                                    verb with no decision behind it. Saving a
                                    key in Edit re-verifies it, which is the
                                    moment the answer can actually change. */}
                                {credential && (
                                  <>
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="text-muted-foreground hover:text-foreground size-7 p-0"
                                      onClick={() => setEditing(credential)}
                                      aria-label={`Edit ${credential.display_name} — key, endpoint and limits`}
                                      title={`Edit ${credential.display_name}`}
                                    >
                                      <Pencil className="size-3.5" aria-hidden />
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="text-muted-foreground hover:text-destructive size-7 p-0"
                                      onClick={() => setRemoving(credential)}
                                      aria-label={`Remove ${credential.display_name}`}
                                      title={`Remove ${credential.display_name}`}
                                    >
                                      <Trash2 className="size-3.5" aria-hidden />
                                    </Button>
                                  </>
                                )}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>

        </>
      )}

      <AddModelDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        catalog={catalogQ.data ?? []}
        catalogLoading={catalogQ.isLoading}
        /* Org-wide: this screen is the Org Admin's, and their onboarding is
           never scoped to a single unit. */
        targetUnits={null}
        allowedByUnit={{}}
        fullCatalog={catalogQ.data ?? []}
        needsApproval={false}
        grantableWorkspaces={grantableWorkspaces}
        initialProvider={providerKind}
        onAdded={() => queryClient.invalidateQueries({ queryKey: ["model"] })}
      />

      <EditProviderDialog
        provider={editing}
        catalog={catalogQ.data ?? []}
        onClose={() => setEditing(null)}
        onSaved={() => queryClient.invalidateQueries({ queryKey: ["model"] })}
      />

      {/* Names the models that lose their key, not just the subscription.
          "Remove Anthropic — EU gateway?" is answerable only if you already
          know what runs on it, and the whole reason this action moved here
          is that this screen does. */}
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {removing?.display_name}?</DialogTitle>
            <DialogDescription>
              The key is deleted from the tenant&apos;s vault. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {removing &&
            (() => {
              // Models this key alone serves — the ones left with nothing.
              const orphaned = rows
                .filter((r) => r.credentialId === removing.id)
                .filter(
                  (r) =>
                    !rows.some(
                      (o) => o.model_id === r.model_id && o.credentialId !== removing.id,
                    ),
                )
                .map((r) => r.model_id);
              return orphaned.length === 0 ? (
                <p className="text-muted-foreground text-[13px]">
                  Every model this subscription serves is also served by another key here.
                </p>
              ) : (
                <div className="border-warning/40 bg-warning/5 rounded-lg border p-3">
                  <p className="text-[13px] font-medium">
                    {orphaned.length} {orphaned.length === 1 ? "model" : "models"} will have no
                    key behind {orphaned.length === 1 ? "it" : "them"}:
                  </p>
                  <p className="text-muted-foreground mt-1 font-mono text-[11.5px]">
                    {orphaned.join(", ")}
                  </p>
                </div>
              );
            })()}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={removeM.isPending}
              onClick={() => removing && removeM.mutate(removing.id)}
            >
              {removeM.isPending ? "Removing…" : "Remove subscription"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ───────── Business Unit Admin's view ─────────

/** One row of the BU Admin's table: a model offered by one of the
 *  credentials the viewer can see for this provider. */
interface BuKeyRow {
  model_id: string;
  offeringId: string;
  isDefault: boolean;
  credential: ModelProvider;
}

/**
 * One provider, from a Business Unit Admin's side.
 *
 * Built from scratch for Task 11 (see the ledger's Task 9.1 / Task 11 entries
 * — this page was locked to every role after a review found Org Admin could
 * reach it by direct URL and add an org-wide credential, a live violation of
 * "Org Admin never adds a key, period"). That constraint is exactly why this
 * branch exists separately from `OrgAdminProviderDetail` rather than as a
 * conditional inside it: the org branch's whole reason for being is grant
 * governance (global/specific visibility, per-unit access, the default
 * model) — none of which a Business Unit Admin does. What they DO do is
 * manage the keys their own unit(s) actually hold and decide which of their
 * projects gets to use one, which is a materially smaller, credential-first
 * view.
 *
 * SCOPING. `listModelProviders(workspaceId)` (already used by the list page's
 * own `!isOrg` branch) returns org-wide connections plus that ONE workspace's
 * own — never another unit's — so calling it once per unit the viewer is
 * bound to (`useScopedBusinessUnits`) and de-duplicating by connection id is
 * enough to guarantee no credential belonging to a unit the viewer isn't
 * bound to ever reaches this component. No client-side filter stands between
 * that guarantee and the render — the query shape IS the boundary.
 *
 * MANAGE ACTIONS ARE OWN-UNIT ONLY. A connection with `workspaceId === null`
 * is the Org Admin's own org-wide key: it is real and useful to see (it may
 * be exactly what covers a model for this unit), but editing, deleting or
 * assigning it to a project is not this admin's call — and the assign
 * endpoint itself rejects an org-wide provider outright (a provider with no
 * workspace can never match a project's), so offering the button there would
 * be a control that always 404s. Those rows render read-only with a
 * "Centrally keyed" marker instead. Edit / Delete / Assign only appear on a
 * row whose credential's `workspaceId` names one of the viewer's own units.
 */
function BuAdminProviderDetail({ providerKind }: { providerKind: string }) {
  const queryClient = useQueryClient();
  const { units, isLoading: unitsLoading } = useScopedBusinessUnits();
  const unitNameById = React.useMemo(
    () => new Map(units.map((u) => [u.id, u.name] as const)),
    [units],
  );

  // One connections query per unit the viewer administers. Each is already
  // scoped server-side to "org-wide + this one unit's own" — see the
  // component doc above.
  const unitProvidersQ = useQueries({
    queries: units.map((u) => ({
      queryKey: qk.model.providers(u.id),
      queryFn: () => listModelProviders(u.id),
      staleTime: 0,
    })),
  });
  const providersLoading = unitsLoading || unitProvidersQ.some((q) => q.isLoading);
  const providersError = unitProvidersQ.find((q) => q.isError);

  const spendQ = useQuery({
    queryKey: qk.cost.spendSeries("model", "all", 6),
    // Already scoped server-side to the caller's readable units (spend.py's
    // `allowed_workspace_ids`), so no per-unit filtering is needed here.
    queryFn: () => getSpendSeries({ groupBy: "model", months: 6 }),
    staleTime: 60_000,
  });
  const catalogQ = useQuery({ queryKey: qk.model.catalog(), queryFn: getModelCatalog });

  // The candidates for "Assign to project" — already scoped server-side to
  // what this viewer may see (projects.py's `visible_project_ids`), narrowed
  // further per-row to the credential's own unit (see `AssignToProjectButton`).
  const projectsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 100 }),
    queryFn: () => listProjects({ pageSize: 100 }),
    staleTime: 30_000,
  });
  const projects = React.useMemo(() => projectsQ.data?.items ?? [], [projectsQ.data]);

  /** Every connection serving this provider that the viewer may see — org-wide
   *  ones plus each of their own units'. De-duplicated: an org-wide key shows
   *  up once per unit query above. */
  const credentials: ModelProvider[] = React.useMemo(() => {
    const byId = new Map<string, ModelProvider>();
    for (const q of unitProvidersQ) {
      for (const p of q.data ?? []) {
        if (p.provider === providerKind) byId.set(p.id, p);
      }
    }
    return [...byId.values()];
  }, [unitProvidersQ, providerKind]);

  /** One row per (model, credential) — the same grain the org table uses, so
   *  a model on two of this unit's keys still gets two rows rather than
   *  silently picking one. Only ENABLED offerings: a disabled one is not
   *  something this unit can actually run, and `assignProviderToProject`
   *  itself only ever pushes the enabled set. */
  const rows: BuKeyRow[] = React.useMemo(
    () =>
      credentials.flatMap((c) =>
        c.offerings
          .filter((o) => o.enabled)
          .map((o) => ({
            model_id: o.model_id,
            offeringId: o.id,
            isDefault: o.is_default,
            credential: c,
          })),
      ),
    [credentials],
  );

  const spendByModel = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const s of spendQ.data?.series ?? []) {
      m.set(s.id, s.points[s.points.length - 1] ?? 0);
    }
    return m;
  }, [spendQ.data]);

  const providerSpend = React.useMemo(() => {
    const seen = new Set<string>();
    return rows.reduce((a, r) => {
      if (seen.has(r.model_id)) return a;
      seen.add(r.model_id);
      return a + (spendByModel.get(r.model_id) ?? 0);
    }, 0);
  }, [rows, spendByModel]);

  const modelSpendRows = React.useMemo(() => {
    const ids = new Set(rows.map((r) => r.model_id));
    return [...ids].map((id) => ({ id, name: id, value: spendByModel.get(id) ?? 0 }));
  }, [rows, spendByModel]);

  const sharedModels = React.useMemo(() => {
    const count = new Map<string, number>();
    for (const r of rows) count.set(r.model_id, (count.get(r.model_id) ?? 0) + 1);
    return new Set([...count.entries()].filter(([, n]) => n > 1).map(([id]) => id));
  }, [rows]);

  const [query, setQuery] = React.useState("");
  const visibleRows = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.model_id.toLowerCase().includes(q) ||
        r.credential.display_name.toLowerCase().includes(q) ||
        (r.credential.workspaceId
          ? (unitNameById.get(r.credential.workspaceId) ?? "").toLowerCase().includes(q)
          : false),
    );
  }, [rows, query, unitNameById]);

  const [editing, setEditing] = React.useState<ModelProvider | null>(null);
  const [removing, setRemoving] = React.useState<ModelProvider | null>(null);

  const invalidateProviders = () => queryClient.invalidateQueries({ queryKey: ["model"] });

  const removeM = useMutation({
    mutationFn: (id: string) => deleteModelProvider(id),
    onSuccess: () => {
      toast.success("Subscription removed");
      setRemoving(null);
      invalidateProviders();
    },
    onError: (err) =>
      toast.error("Couldn't remove subscription", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const assignM = useMutation({
    mutationFn: ({ credentialId, projectId }: { credentialId: string; projectId: string }) =>
      assignProviderToProject(credentialId, projectId),
    onSuccess: (_result, vars) => {
      const project = projects.find((p) => String(p.id) === vars.projectId);
      toast.success(project ? `Assigned to ${project.name}` : "Key assigned to project");
    },
    onError: (err) =>
      toast.error("Couldn't assign this key to that project", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const label = providerLabel(providerKind);

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <div>
        <Link
          href="/admin/models"
          className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1 font-mono text-[11px] transition-colors"
        >
          <ArrowLeft className="size-3" aria-hidden />
          All providers
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-[32px] leading-[1.05] font-bold tracking-[-0.03em]">
              {label}
            </h1>
            <p className="text-muted-foreground mt-1 text-[13px]">
              What this provider costs your {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}, which key
              serves each model, and which projects it&apos;s assigned to.
            </p>
          </div>
          {spendQ.data && (
            <div className="text-right">
              <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                This month
              </p>
              <p className="font-display text-[26px] leading-none font-bold tabular-nums">
                {usd(providerSpend)}
              </p>
            </div>
          )}
        </div>
      </div>

      {providersError ? (
        <ErrorState
          title="Couldn't load this provider"
          onRetry={() => unitProvidersQ.forEach((q) => void q.refetch())}
        />
      ) : providersLoading ? (
        <LoadingState variant="card" />
      ) : units.length === 0 ? (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            You aren&apos;t bound to any {BUSINESS_UNIT_LABEL.toLowerCase()} yet, so there&apos;s
            nothing here to manage.
          </p>
        </div>
      ) : (
        <>
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                  Cost by model
                </h2>
                <span className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                  This month
                </span>
              </div>
              <p className="text-muted-foreground text-[12px]">
                {modelSpendRows.length} {label} {modelSpendRows.length === 1 ? "model" : "models"}{" "}
                your {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} can use, ranked by what they cost
                this month.
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
                <Stat label="This month" value={spendQ.data ? usd(providerSpend) : "—"} />
                <Stat label="Models" value={String(modelSpendRows.length)} />
                <Stat label="Keys" value={String(credentials.length)} />
              </div>
              <SpendRankedBars
                rows={modelSpendRows}
                emptyLabel={`No ${label} spend this month.`}
              />
            </CardContent>
          </Card>

          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                Models &amp; keys
              </h2>
              <p className="text-muted-foreground text-[12px]">
                Manage the key serving each model, and assign it to one of your projects.
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              {rows.length === 0 ? (
                <p className="text-muted-foreground text-[12.5px]">
                  {label} isn&apos;t keyed for your {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} yet.
                  Add a key from the{" "}
                  <Link href="/admin/models" className="text-brand-bright underline underline-offset-2">
                    Models
                  </Link>{" "}
                  page.
                </p>
              ) : (
                <>
                  <div className="flex items-center justify-between gap-3">
                    <div className="border-line-soft bg-surface-1 flex max-w-sm flex-1 items-center gap-2 rounded-lg border px-2.5">
                      <Search className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                      <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder={`Search ${rows.length} models or credentials…`}
                        aria-label="Search models or credentials"
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
                    {query.trim() && (
                      <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                        {visibleRows.length} of {rows.length}
                      </span>
                    )}
                  </div>

                  {visibleRows.length === 0 ? (
                    <p className="text-muted-foreground py-4 text-center text-[12.5px]">
                      No model or credential matches &ldquo;{query.trim()}&rdquo;.
                    </p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[640px] border-collapse text-left">
                        <thead>
                          <tr className="border-line-soft border-b">
                            <th className="text-muted-foreground py-2 pr-3 font-mono text-[10px] font-semibold tracking-wider uppercase">
                              Model
                            </th>
                            <th className="text-muted-foreground px-2 py-2 text-right font-mono text-[10px] font-semibold tracking-wider uppercase">
                              Spend
                            </th>
                            <th className="text-muted-foreground px-2 py-2 font-mono text-[10px] font-semibold tracking-wider uppercase">
                              Credential
                            </th>
                            <th className="text-muted-foreground px-2 py-2 text-right font-mono text-[10px] font-semibold tracking-wider uppercase">
                              Manage
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-line-soft divide-y">
                          {visibleRows.map((row) => {
                            const c = row.credential;
                            // Own-unit key only: an org-wide credential's
                            // workspaceId is null, and this admin doesn't
                            // edit, delete or assign the Org Admin's key —
                            // see the component doc above.
                            const own = !!c.workspaceId;
                            return (
                              <tr key={`${row.model_id}::${c.id}`}>
                                <td className="py-2.5 pr-3">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="font-mono text-[12px]">{row.model_id}</span>
                                    {row.isDefault && (
                                      <span className="text-success bg-success/10 border-success/30 shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] font-semibold tracking-wide uppercase">
                                        Default
                                      </span>
                                    )}
                                  </div>
                                </td>

                                <td className="px-2 py-2.5 text-right font-mono text-[12px] tabular-nums">
                                  {spendQ.data ? usd(spendByModel.get(row.model_id) ?? 0) : "—"}
                                  {sharedModels.has(row.model_id) && (
                                    <span className="text-muted-foreground ml-1 block font-sans text-[10px] font-normal">
                                      across all keys
                                    </span>
                                  )}
                                </td>

                                <td className="px-2 py-2.5">
                                  <div className="space-y-0.5">
                                    <span className="block text-[11.5px]">{c.display_name}</span>
                                    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                                      {c.hasKey === false && (
                                        <span className="text-warning inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase">
                                          <KeyRound className="size-2.5" aria-hidden />
                                          Holds no key
                                        </span>
                                      )}
                                      {c.hasKey && c.status !== "valid" && (
                                        <span className="text-muted-foreground font-mono text-[9.5px] tracking-wide uppercase">
                                          {c.status}
                                        </span>
                                      )}
                                      {own ? (
                                        <span className="text-muted-foreground font-mono text-[9.5px] tracking-wide uppercase">
                                          {unitNameById.get(c.workspaceId as string) ??
                                            "Unit-scoped"}{" "}
                                          only
                                        </span>
                                      ) : (
                                        <span className="text-muted-foreground inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase">
                                          <Globe className="size-2.5" aria-hidden />
                                          Centrally keyed
                                        </span>
                                      )}
                                    </span>
                                  </div>
                                </td>

                                <td className="py-2.5 pl-2">
                                  <div className="flex items-center justify-end gap-1">
                                    {own ? (
                                      <>
                                        <AssignToProjectButton
                                          workspaceId={c.workspaceId as string}
                                          projects={projects}
                                          projectsLoading={projectsQ.isLoading}
                                          pending={assignM.isPending}
                                          onAssign={(projectId) =>
                                            assignM.mutate({ credentialId: c.id, projectId })
                                          }
                                        />
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="text-muted-foreground hover:text-foreground size-7 p-0"
                                          onClick={() => setEditing(c)}
                                          aria-label={`Edit ${c.display_name} — key, endpoint and limits`}
                                          title={`Edit ${c.display_name}`}
                                        >
                                          <Pencil className="size-3.5" aria-hidden />
                                        </Button>
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="text-muted-foreground hover:text-destructive size-7 p-0"
                                          onClick={() => setRemoving(c)}
                                          aria-label={`Remove ${c.display_name}`}
                                          title={`Remove ${c.display_name}`}
                                        >
                                          <Trash2 className="size-3.5" aria-hidden />
                                        </Button>
                                      </>
                                    ) : (
                                      <span className="text-muted-foreground font-mono text-[10px] tracking-wide uppercase">
                                        Managed by your Org Admin
                                      </span>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}

      <EditProviderDialog
        provider={editing}
        catalog={catalogQ.data ?? []}
        onClose={() => setEditing(null)}
        onSaved={() => queryClient.invalidateQueries({ queryKey: ["model"] })}
      />

      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {removing?.display_name}?</DialogTitle>
            <DialogDescription>
              The key is deleted from the tenant&apos;s vault. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {removing &&
            (() => {
              const orphaned = rows
                .filter((r) => r.credential.id === removing.id)
                .filter(
                  (r) =>
                    !rows.some(
                      (o) => o.model_id === r.model_id && o.credential.id !== removing.id,
                    ),
                )
                .map((r) => r.model_id);
              return orphaned.length === 0 ? (
                <p className="text-muted-foreground text-[13px]">
                  Every model this subscription serves is also served by another key here.
                </p>
              ) : (
                <div className="border-warning/40 bg-warning/5 rounded-lg border p-3">
                  <p className="text-[13px] font-medium">
                    {orphaned.length} {orphaned.length === 1 ? "model" : "models"} will have no
                    key behind {orphaned.length === 1 ? "it" : "them"}:
                  </p>
                  <p className="text-muted-foreground mt-1 font-mono text-[11.5px]">
                    {orphaned.join(", ")}
                  </p>
                </div>
              );
            })()}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={removeM.isPending}
              onClick={() => removing && removeM.mutate(removing.id)}
            >
              {removeM.isPending ? "Removing…" : "Remove subscription"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * "Assign to project" — a small Command+Popover picker matching
 * `UnitAccessPicker`'s own structure, narrowed to the ONE business unit this
 * credential belongs to. The backend's `assign_provider_to_project` rejects
 * any project outside that unit outright (a provider's `workspace_id` must
 * equal the project's), so listing anything wider would just be a picker full
 * of choices that 403 on selection.
 */
function AssignToProjectButton({
  workspaceId,
  projects,
  projectsLoading,
  pending,
  onAssign,
}: {
  workspaceId: string;
  projects: Project[];
  projectsLoading: boolean;
  pending: boolean;
  onAssign: (projectId: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const scoped = React.useMemo(
    () => projects.filter((p) => p.workspaceId === workspaceId),
    [projects, workspaceId],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-foreground size-7 p-0"
          aria-label="Assign this key to a project"
          title="Assign this key to one of your projects"
          disabled={pending}
        >
          <FolderPlus className="size-3.5" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[min(20rem,90vw)] p-0" align="end">
        {projectsLoading ? (
          <p className="text-muted-foreground p-3 text-[12px]">Loading projects…</p>
        ) : scoped.length === 0 ? (
          <p className="text-muted-foreground p-3 text-[12px]">
            No projects in this {BUSINESS_UNIT_LABEL.toLowerCase()} yet.
          </p>
        ) : (
          <Command filter={substringFilter}>
            <CommandInput placeholder="Search projects…" />
            <CommandList className="max-h-[min(50vh,16rem)]">
              <CommandEmpty>No matching project.</CommandEmpty>
              {scoped.map((p) => {
                const id = String(p.id);
                return (
                  <CommandItem
                    key={id}
                    value={`${p.name} ${id}`}
                    onSelect={() => {
                      onAssign(id);
                      setOpen(false);
                    }}
                  >
                    <span className="truncate text-[12.5px]">{p.name}</span>
                  </CommandItem>
                );
              })}
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  );
}

// ───────── Shared bits ─────────

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border-line-soft bg-surface-1 rounded-xl border px-3 py-2.5">
      <p className="text-muted-foreground font-mono text-[9.5px] tracking-[0.12em] uppercase">
        {label}
      </p>
      <p className="font-display mt-1 text-[20px] leading-none font-bold tabular-nums">{value}</p>
      {sub && <p className="text-muted-foreground mt-1 text-[11px]">{sub}</p>}
    </div>
  );
}
