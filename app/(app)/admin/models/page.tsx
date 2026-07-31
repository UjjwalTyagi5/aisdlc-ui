"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  Building2,
  Check,
  CheckCircle2,
  ChevronsUpDown,
  Clock,
  Globe,
  Loader2,
  Lock,
  Pencil,
  Plus,
  Trash2,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

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
import { SpendBreakdownCard } from "@/components/app/spend-breakdown-card";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useRawSession } from "@/components/auth/session-provider";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import {
  addModelProvider,
  deleteModelProvider,
  getBuAllowedModels,
  getModelCatalog,
  getOrgAllowedModels,
  listModelProviders,
  setBuAllowedModels,
  setModelDefault,
  setOrgAllowedModels,
  updateModelProvider,
  verifyModelProvider,
} from "@/lib/api/models";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { qk } from "@/lib/api/query-keys";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type {
  CatalogProvider,
  ModelAllowEntry,
  ModelOffering,
  ModelProvider,
  ModelProviderKind,
  ModelProviderStatus,
} from "@/lib/schemas/model";

const PROVIDER_LABEL: Record<ModelProviderKind, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
};

function providerLabel(kind: ModelProviderKind): string {
  return PROVIDER_LABEL[kind] ?? kind;
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

/** Keep only the catalog entries an allow-list actually permits — used to
 *  scope a BU Admin's onboarding dialog to org-allowed models, and a Project
 *  Admin's to BU-allowed models (the cascade's enforcement point). */
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

// ───────── Allow-list card — checkbox grid + explicit Save (org and BU tiers) ─────────

function ModelAllowListCard({
  icon: Icon,
  title,
  description,
  catalog,
  value,
  loading,
  saving,
  onSave,
  emptyHint,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  /** The universe of models this tier may choose from — already scoped to
   *  the tier above (full catalog for org; org-allowed for bu). */
  catalog: CatalogProvider[];
  value: ModelAllowEntry[];
  loading?: boolean;
  saving?: boolean;
  onSave: (entries: ModelAllowEntry[]) => void;
  emptyHint?: string;
}) {
  const [draft, setDraft] = React.useState<Set<string>>(new Set());
  React.useEffect(() => {
    setDraft(new Set(value.map((e) => `${e.provider}::${e.model_id}`)));
  }, [value]);

  const key = (provider: string, model_id: string) => `${provider}::${model_id}`;
  const toggle = (provider: string, model_id: string) => {
    const k = key(provider, model_id);
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  };

  const originalKey = value
    .map((e) => key(e.provider, e.model_id))
    .sort()
    .join(",");
  const draftKey = [...draft].sort().join(",");
  const dirty = originalKey !== draftKey;

  const handleSave = () => {
    const entries: ModelAllowEntry[] = [];
    for (const k of draft) {
      const [provider, model_id] = k.split("::");
      if (provider && model_id) entries.push({ provider, model_id });
    }
    onSave(entries);
  };

  return (
    <Card className="border-line-soft bg-panel-elevated">
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <div
            aria-hidden
            className="border-line-soft bg-surface-2 text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg border"
          >
            <Icon className="size-4" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-[14px] font-bold tracking-[-0.01em]">{title}</h3>
            <p className="text-muted-foreground mt-0.5 text-[12px]">{description}</p>
          </div>
          <Button size="sm" onClick={handleSave} disabled={!dirty || saving} aria-busy={saving}>
            {saving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {loading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : catalog.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">
            {emptyHint ?? "Nothing available to allow-list yet."}
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map((c) => (
              <div key={c.provider} className="space-y-1.5">
                <p className="text-muted-foreground font-mono text-[10.5px] font-semibold uppercase tracking-wider">
                  {c.label}
                </p>
                <ul className="space-y-1">
                  {c.models.map((m) => {
                    const checkboxId = `allow-${c.provider}-${m.model_id}`;
                    return (
                      <li key={m.model_id} className="flex items-center gap-2">
                        <Checkbox
                          id={checkboxId}
                          checked={draft.has(key(c.provider, m.model_id))}
                          onCheckedChange={() => toggle(c.provider, m.model_id)}
                        />
                        <Label htmlFor={checkboxId} className="cursor-pointer truncate font-mono text-[12px] font-normal">
                          {m.model_id}
                        </Label>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function ModelProvidersPage() {
  const queryClient = useQueryClient();
  const session = useRawSession();
  const role = effectivePlatformRole(session);
  const { active: activeWorkspace } = useActiveWorkspace();
  const activeWsId = activeWorkspace?.id ?? null;

  // Three-tier cascade: an Org Admin's own onboarding is org-wide
  // (workspaceId null); a BU Admin's and a Project Admin's are scoped to
  // their active business unit.
  const scope: "org" | "bu" | "project" | null =
    role === "org_admin" ? "org" : role === "bu_admin" ? "bu" : role === "project_admin" ? "project" : null;
  const viewWorkspaceId = scope === "org" ? null : activeWsId;
  const needsApproval = scope === "project";

  const providersQ = useQuery({
    queryKey: qk.model.providers(viewWorkspaceId),
    queryFn: () => listModelProviders(viewWorkspaceId),
    staleTime: 0,
  });

  const catalogQ = useQuery({
    queryKey: qk.model.catalog(),
    queryFn: () => getModelCatalog(),
  });

  const orgAllowedQ = useQuery({
    queryKey: qk.model.orgAllowed(),
    queryFn: () => getOrgAllowedModels(),
  });

  const buAllowedQ = useQuery({
    queryKey: qk.model.buAllowed(viewWorkspaceId ?? ""),
    queryFn: () => getBuAllowedModels(viewWorkspaceId as string),
    enabled: scope !== "org" && !!viewWorkspaceId,
  });

  const [addOpen, setAddOpen] = React.useState(false);
  const [editFor, setEditFor] = React.useState<ModelProvider | null>(null);
  const [removeFor, setRemoveFor] = React.useState<ModelProvider | null>(null);
  const [verifyingId, setVerifyingId] = React.useState<string | null>(null);

  const invalidateProviders = () =>
    queryClient.invalidateQueries({ queryKey: qk.model.providers(viewWorkspaceId) });

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

  const orgAllowMutation = useMutation({
    mutationFn: (entries: ModelAllowEntry[]) => setOrgAllowedModels(entries),
    onSuccess: () => {
      toast.success("Global allow-list updated");
      queryClient.invalidateQueries({ queryKey: qk.model.orgAllowed() });
    },
    onError: (err) =>
      toast.error("Couldn't update allow-list", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const buAllowMutation = useMutation({
    mutationFn: (entries: ModelAllowEntry[]) => setBuAllowedModels(viewWorkspaceId as string, entries),
    onSuccess: () => {
      toast.success(`${BUSINESS_UNIT_LABEL} allow-list updated`);
      queryClient.invalidateQueries({ queryKey: qk.model.buAllowed(viewWorkspaceId ?? "") });
    },
    onError: (err) =>
      toast.error("Couldn't update allow-list", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const verifyMutation = useMutation({
    mutationFn: (id: string) => {
      setVerifyingId(id);
      return verifyModelProvider(id);
    },
    onSuccess: (result) => {
      if (result.status === "valid") {
        toast.success("Provider verified ✓");
      } else {
        toast.error("Key rejected — verification failed");
      }
      invalidateProviders();
    },
    onError: (err) =>
      toast.error("Verification failed", {
        description: err instanceof Error ? err.message : undefined,
      }),
    onSettled: () => setVerifyingId(null),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => deleteModelProvider(id),
    onSuccess: () => {
      toast.success("Provider removed");
      invalidateProviders();
    },
    onError: (err) =>
      toast.error("Couldn't remove provider", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  if (providersQ.isLoading) {
    return (
      <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
        <LoadingState variant="list" rows={3} />
      </div>
    );
  }

  if (providersQ.isError) {
    return (
      <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
        <ApiErrorState
          title="Couldn't load model providers"
          error={
            providersQ.error && "code" in providersQ.error && "message" in providersQ.error
              ? (providersQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(providersQ.error && "code" in providersQ.error)
              ? providersQ.error instanceof Error
                ? providersQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => providersQ.refetch()}
        />
      </div>
    );
  }

  if (!hasPermission(session, "model:manage") || !scope) {
    return (
      <RestrictedAccess description="Model providers require the model:manage permission." />
    );
  }

  const providers = providersQ.data ?? [];
  const catalog = catalogQ.data ?? [];
  const orgAllowed = orgAllowedQ.data ?? [];
  const buAllowed = buAllowedQ.data ?? [];

  // The onboarding dialog's provider picker is scoped by the cascade: an Org
  // Admin sees the full catalog (they're the ones defining what's allowed at
  // all); a BU Admin sees only org-allowed models; a Project Admin sees only
  // their BU's allowed subset.
  const effectiveCatalog =
    scope === "org" ? catalog : scope === "bu" ? filterCatalogToAllowed(catalog, orgAllowed) : filterCatalogToAllowed(catalog, buAllowed);

  // The single org-wide default across every provider's enabled offerings —
  // only Org Admin's own org-wide providers participate in this.
  const defaultOfferingId = providers
    .flatMap((p) => p.offerings)
    .find((o) => o.is_default)?.id;

  const HEADER_COPY: Record<"org" | "bu" | "project", { eyebrow: string; title: string; body: string }> = {
    org: {
      eyebrow: "Govern",
      title: "Model Providers",
      body: "Curate the org-wide model catalogue and bring your organization's own LLM keys. Agent runs use these models — and only these.",
    },
    bu: {
      eyebrow: "Govern",
      title: "Model Providers",
      body: `Choose which org-approved models this business unit can use, and connect ${BUSINESS_UNIT_LABEL.toLowerCase()}-scoped credentials directly.`,
    },
    project: {
      eyebrow: "Configure",
      title: "Model Providers",
      body: `Onboard a model with your own credentials from your business unit's allowed list. New connections need your ${BUSINESS_UNIT_LABEL} Admin's approval before they're usable.`,
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
          <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            {copy.eyebrow}
          </div>
          <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
            {copy.title}
          </h1>
          <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">{copy.body}</p>
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

      {/* What the allow-lists below are actually costing, per model. */}
      <SpendBreakdownCard groupBy="model" />

      {scope === "org" && (
        <ModelAllowListCard
          icon={Globe}
          title="Global model allow-list"
          description="Which models any business unit may be given access to. Nothing outside this list can be BU-allowed or onboarded anywhere in the org."
          catalog={catalog}
          value={orgAllowed}
          loading={catalogQ.isLoading || orgAllowedQ.isLoading}
          saving={orgAllowMutation.isPending}
          onSave={(entries) => orgAllowMutation.mutate(entries)}
        />
      )}

      {scope === "bu" && (
        <ModelAllowListCard
          icon={Building2}
          title={`${BUSINESS_UNIT_LABEL} model allow-list`}
          description={`A subset of the org's allow-list — only these models can be onboarded with credentials in ${activeWorkspace?.displayName ?? "this business unit"}.`}
          catalog={filterCatalogToAllowed(catalog, orgAllowed)}
          value={buAllowed}
          loading={catalogQ.isLoading || orgAllowedQ.isLoading || buAllowedQ.isLoading}
          saving={buAllowMutation.isPending}
          onSave={(entries) => buAllowMutation.mutate(entries)}
          emptyHint="Your Org Admin hasn't allow-listed any models org-wide yet."
        />
      )}

      {scope === "project" && (
        <div className="border-line-soft bg-surface-1 rounded-xl border p-4">
          <p className="text-[13px] font-medium">
            Models available to onboard in {activeWorkspace?.displayName ?? "this business unit"}
          </p>
          {buAllowedQ.isLoading ? (
            <p className="text-muted-foreground mt-1 text-[12.5px]">Loading…</p>
          ) : buAllowed.length === 0 ? (
            <p className="text-muted-foreground mt-1 text-[12.5px]">
              Your {BUSINESS_UNIT_LABEL} Admin hasn&apos;t allowed any models for this business unit yet —
              ask them to add some before onboarding credentials.
            </p>
          ) : (
            <>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {buAllowed.map((e) => (
                  <li
                    key={`${e.provider}::${e.model_id}`}
                    className="border-line-soft bg-surface-2 text-muted-foreground rounded-full border px-2 py-0.5 font-mono text-[10.5px]"
                  >
                    {e.model_id}
                  </li>
                ))}
              </ul>
              {/* This page onboards credentials; choosing which of these a
                  given project actually runs on is per-project, so it lives
                  with the project rather than here. */}
              <p className="text-muted-foreground mt-2.5 text-[12.5px]">
                Choose which of these a project uses on its{" "}
                <span className="text-foreground">Settings → Model</span> tab.
              </p>
            </>
          )}
        </div>
      )}

      {providers.length === 0 ? (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            {scope === "org"
              ? "No model provider configured yet. Agent runs are blocked until an admin adds and verifies one."
              : `No model provider onboarded in ${activeWorkspace?.displayName ?? "this business unit"} yet.`}
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
        <RadioGroup
          value={scope === "org" ? (defaultOfferingId ?? "") : ""}
          onValueChange={(v) => scope === "org" && setDefaultMutation.mutate(v)}
          className="grid gap-3 sm:grid-cols-2"
          aria-label={scope === "org" ? "Org-wide default model" : "Enabled models"}
        >
          {providers.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              radioDisabled={scope !== "org"}
              verifying={verifyMutation.isPending && verifyingId === p.id}
              onVerify={() => verifyMutation.mutate(p.id)}
              onEdit={() => setEditFor(p)}
              onRemove={() => setRemoveFor(p)}
            />
          ))}
        </RadioGroup>
      )}

      <AddProviderDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        catalog={effectiveCatalog}
        catalogLoading={catalogQ.isLoading || (scope !== "org" && (orgAllowedQ.isLoading || buAllowedQ.isLoading))}
        workspaceId={viewWorkspaceId}
        needsApproval={needsApproval}
        onAdded={invalidateProviders}
      />

      <EditProviderDialog
        provider={editFor}
        catalog={catalog}
        onClose={() => setEditFor(null)}
        onSaved={invalidateProviders}
      />

      <RemoveConfirm
        provider={removeFor}
        onClose={() => setRemoveFor(null)}
        onConfirm={(p) => {
          setRemoveFor(null);
          removeMutation.mutate(p.id);
        }}
      />
    </div>
  );
}

// ───────── Provider card ─────────

function ProviderCard({
  provider,
  verifying,
  onVerify,
  onEdit,
  onRemove,
  radioDisabled,
}: {
  provider: ModelProvider;
  verifying?: boolean;
  onVerify: () => void;
  onEdit: () => void;
  onRemove: () => void;
  /** True outside org scope — no scoped provider participates in the single
   *  org-wide default, so the radio is shown inert rather than interactive. */
  radioDisabled?: boolean;
}) {
  const label = providerLabel(provider.provider);
  const enabledOfferings = provider.offerings.filter((o) => o.enabled);
  const pending = provider.approvalStatus === "pending_approval";
  const rejected = provider.approvalStatus === "rejected";

  return (
    <Card className="border-line-soft bg-panel-elevated flex flex-col overflow-hidden shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_4px_14px_-6px_oklch(0_0_0_/_0.35)] transition-shadow hover:shadow-[0_6px_20px_-8px_oklch(0_0_0_/_0.45)]">
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <ProviderGlyph label={provider.display_name || label} />
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
              {provider.display_name}
            </h3>
            <p className="text-muted-foreground mt-0.5 truncate text-[12px]">{label}</p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <StatusPill status={provider.status} />
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
          <ul className="space-y-1.5">
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

        <div className="mt-auto flex gap-1.5">
          <Button
            variant="outline"
            size="sm"
            onClick={onVerify}
            disabled={verifying}
            aria-busy={verifying}
            aria-label={`Test ${provider.display_name}`}
            className="border-line-soft"
          >
            {verifying ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <CheckCircle2 className="size-4" aria-hidden />
            )}
            {verifying ? "Testing…" : "Test"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onEdit}
            aria-label={`Edit ${provider.display_name}`}
            className="border-line-soft"
          >
            <Pencil className="size-4" aria-hidden />
            Edit
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onRemove}
            aria-label={`Remove ${provider.display_name}`}
            className="border-line-soft ml-auto"
          >
            <Trash2 className="size-4" aria-hidden />
            Remove
          </Button>
        </div>
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
  workspaceId,
  needsApproval,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalog: CatalogProvider[];
  catalogLoading: boolean;
  /** null = org-wide (Org Admin); a workspace id scopes the connection to
   *  that business unit (BU Admin or Project Admin onboarding). */
  workspaceId: string | null;
  /** True for a Project Admin's onboarding — created pending, needs their BU
   *  Admin's approval before it's verified or usable. */
  needsApproval: boolean;
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

  const selectedCatalog = catalog.find((c) => c.provider === provider);
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
      setPending(false);
    }
  }, [open]);

  // Picking a provider just prefills the display name; the admin chooses models.
  const onProviderChange = (slug: string) => {
    setProvider(slug);
    setProviderOpen(false);
    setEnabled({});
    setModelQuery("");
    const label = catalog.find((c) => c.provider === slug)?.label ?? slug;
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

  const sharedValid = displayName.trim().length > 0 && apiKey.trim().length > 0;
  const canSubmit =
    !!provider && sharedValid && enabledModels.length + validCustomModels.length > 0;

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
        workspaceId,
      });
      if (needsApproval) {
        toast.info("Sent for approval", {
          description: `A Business Unit Admin needs to approve ${created.display_name} before it's usable.`,
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
              ? "The key is stored in the tenant's secrets vault and never shown again. Your Business Unit Admin approves before it's live — no live verification runs until then."
              : "The key is stored in the tenant's secrets vault and never shown again. We run a 1-token live probe to verify it on save."}
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
          {/* Provider — searchable combobox over LiteLLM's full provider catalog */}
          <div className="space-y-1.5">
            <Label>Provider</Label>
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
                      {catalog.map((c) => (
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
            <p className="text-muted-foreground text-[11px]">
              Any provider LiteLLM supports. Pick one to see its models with pricing.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="display-name">Display name</Label>
            <Input
              id="display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Mistral (production)"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="api-key">API key</Label>
            <Input
              id="api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-…"
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="api-base">
              API base{" "}
              <span className="text-muted-foreground/60 font-normal normal-case">(optional)</span>
            </Label>
            <Input
              id="api-base"
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="https://your-gateway.internal/v1 — for self-hosted / gateway"
              autoComplete="off"
              className="font-mono"
            />
          </div>

          {/* Models for the chosen provider — auto-listed from LiteLLM, searchable,
              pricing prefilled. */}
          {provider && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Models</Label>
                <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
                  USD / 1M tokens
                </span>
              </div>
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
            </div>
          )}
        </div>

        {/* Optional per-model usage limits — applied to every model added here. */}
        <div className="space-y-2">
          <div className="text-[13px] font-medium">Usage limits (optional)</div>
          <p className="text-muted-foreground text-[12px]">
            Applied to every model added here. Leave blank for no limit. RPM (requests/min)
            is enforced live; TPM (tokens/min) and cost ($/month) are recorded.
          </p>
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
            {pending ? "Testing…" : "Test & Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ───────── Edit provider dialog ─────────

function EditProviderDialog({
  provider,
  catalog,
  onClose,
  onSaved,
}: {
  provider: ModelProvider | null;
  catalog: CatalogProvider[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const open = !!provider;
  const [displayName, setDisplayName] = React.useState("");
  const [enabled, setEnabled] = React.useState<Record<string, boolean>>({});
  const [modelQuery, setModelQuery] = React.useState("");
  const [pending, setPending] = React.useState(false);

  // Snapshot of the opened provider — used for dirty-checking and the default-model lock.
  // The org-wide default may only be disabled from the default radio, never from here,
  // so a disabled+default offering (which would block all runs) can never be produced.
  const original = React.useMemo(() => {
    if (!provider) return null;
    const enabledMap: Record<string, boolean> = {};
    for (const o of provider.offerings) if (o.enabled) enabledMap[o.model_id] = true;
    const lockedModelId =
      provider.offerings.find((o) => o.is_default && o.enabled)?.model_id ?? null;
    return { displayName: provider.display_name, enabledKeys: Object.keys(enabledMap).sort(), lockedModelId };
  }, [provider]);

  // Re-seed the form each time a different provider is opened.
  React.useEffect(() => {
    if (!provider) return;
    setDisplayName(provider.display_name);
    const seed: Record<string, boolean> = {};
    for (const o of provider.offerings) if (o.enabled) seed[o.model_id] = true;
    setEnabled(seed);
    setModelQuery("");
    setPending(false);
  }, [provider]);

  const models = provider
    ? (catalog.find((c) => c.provider === provider.provider)?.models ?? [])
    : [];
  const filteredModels = models.filter((m) =>
    m.model_id.toLowerCase().includes(modelQuery.trim().toLowerCase()),
  );

  const enabledModels = Object.entries(enabled)
    .filter(([, v]) => v)
    .map(([k]) => k);

  const trimmedName = displayName.trim();
  const dirty =
    !!original &&
    (trimmedName !== original.displayName ||
      enabledModels.slice().sort().join(" ") !== original.enabledKeys.join(" "));

  const canSubmit = trimmedName.length > 0 && enabledModels.length > 0 && dirty;
  const lockedModelId = original?.lockedModelId ?? null;

  const handleSubmit = async () => {
    if (!provider || !canSubmit || pending) return;
    setPending(true);
    try {
      await updateModelProvider(provider.id, {
        display_name: trimmedName,
        enabled_models: enabledModels,
      });
      toast.success("Provider updated");
      onSaved();
      onClose();
    } catch (err) {
      toast.error("Couldn't update provider", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !pending && !v && onClose()}>
      <DialogContent className="max-h-[92vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-display">Edit {provider?.display_name}</DialogTitle>
          <DialogDescription>
            Rename this connection or change which models it offers. The provider and API key
            can&apos;t be changed here &mdash; remove and re-add to rotate the key.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="edit-display-name">Display name</Label>
            <Input
              id="edit-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Anthropic (production)"
            />
          </div>

          {models.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Enabled models</Label>
                <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
                  USD / 1M tokens
                </span>
              </div>
              {models.length > 6 && (
                <Input
                  value={modelQuery}
                  onChange={(e) => setModelQuery(e.target.value)}
                  placeholder={`Search ${models.length} models…`}
                  autoComplete="off"
                  className="h-9"
                />
              )}
              <ul className="divide-line-soft border-line-soft max-h-56 divide-y overflow-y-auto rounded-xl border">
                {filteredModels.length === 0 ? (
                  <li className="text-muted-foreground p-3 text-[12px]">No models match.</li>
                ) : (
                  filteredModels.map((m) => {
                    const checkboxId = `edit-enable-${m.model_id}`;
                    const isLocked = m.model_id === lockedModelId;
                    const priced =
                      typeof m.input_price_per_million === "number" &&
                      typeof m.output_price_per_million === "number";
                    return (
                      <li key={m.model_id} className="flex items-center gap-3 p-2.5">
                        <Checkbox
                          id={checkboxId}
                          checked={enabled[m.model_id] ?? false}
                          disabled={isLocked}
                          onCheckedChange={(v) =>
                            setEnabled((prev) => ({ ...prev, [m.model_id]: v === true }))
                          }
                        />
                        <Label
                          htmlFor={checkboxId}
                          className={cn(
                            "flex min-w-0 flex-1 items-center justify-between gap-2 font-normal",
                            isLocked ? "cursor-default" : "cursor-pointer",
                          )}
                        >
                          <span className="truncate font-mono text-[12px]">{m.model_id}</span>
                          {isLocked ? (
                            <span className="text-muted-foreground inline-flex shrink-0 items-center gap-1 font-mono text-[10px] uppercase">
                              <Lock className="size-3" aria-hidden />
                              Org default
                            </span>
                          ) : priced ? (
                            <span className="text-muted-foreground shrink-0 font-mono text-[10.5px] tabular-nums">
                              ${m.input_price_per_million} / ${m.output_price_per_million}
                            </span>
                          ) : m.tier_hint ? (
                            <span className="text-muted-foreground shrink-0 font-mono text-[10px] uppercase">
                              {m.tier_hint}
                            </span>
                          ) : null}
                        </Label>
                      </li>
                    );
                  })
                )}
              </ul>
              {lockedModelId && (
                <p className="text-muted-foreground text-[12px]">
                  <span className="font-mono text-[11px]">{lockedModelId}</span> is your org&apos;s
                  default and can&apos;t be disabled here. Set a different default first.
                </p>
              )}
              {enabledModels.length === 0 && (
                <p className="text-warning text-[12px]">Enable at least one model.</p>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={onClose}
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
            {pending ? "Saving…" : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ───────── Remove confirm ─────────

function RemoveConfirm({
  provider,
  onClose,
  onConfirm,
}: {
  provider: ModelProvider | null;
  onClose: () => void;
  onConfirm: (p: ModelProvider) => void;
}) {
  const open = !!provider;
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <Trash2 className="text-destructive size-5" aria-hidden />
            Remove {provider?.display_name}?
          </DialogTitle>
          <DialogDescription>
            The stored key is deleted from the secrets vault. Runs using this provider&apos;s default
            model will be blocked until another provider is configured.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="border-line-soft">
            Keep
          </Button>
          <Button variant="destructive" onClick={() => provider && onConfirm(provider)}>
            Remove
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
