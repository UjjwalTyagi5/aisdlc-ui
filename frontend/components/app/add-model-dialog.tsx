"use client";

import * as React from "react";
import { toast } from "sonner";
import { Check, ChevronsUpDown, Clock, Loader2, Plus, Trash2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
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
import { addModelProvider, probeModelProvider, verifyModelProvider } from "@/lib/api/models";
import { providerLabel } from "@/lib/models/provider-labels";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { GrantVisibility } from "@/lib/schemas/grant";
import type { CatalogProvider, ModelAllowEntry, ModelProviderKind } from "@/lib/schemas/model";

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

/** Keep only the catalog entries a grant actually permits — the cascade's
 *  enforcement point in the UI: a BU Admin's and a Project Admin's onboarding
 *  dialog can only offer models the Org Admin granted their unit. */
export function filterCatalogToAllowed(
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

/**
 * Bring models into the platform, with the key that serves them.
 *
 * ONE DIALOG, TWO DOORS. Reached from the provider list it asks which vendor
 * first, because that is genuinely open. Reached from a provider's own screen
 * (`initialProvider`) the vendor is already the answer — you are standing on
 * Anthropic, so a combobox defaulted to "Search providers…" would ask you to
 * re-state where you are, and picking a different one would silently take the
 * save somewhere other than the page you launched it from. So the provider is
 * pre-selected and the step becomes a statement rather than a question.
 *
 * Either way the SHAPE of the write is identical: a credential carrying the
 * models chosen for it. "Add a model" and "add a provider" are the same act
 * seen from two heights, and giving them two forms would mean two sets of rules
 * about pricing, limits and reach.
 */
export function AddModelDialog({
  open,
  onOpenChange,
  catalog,
  catalogLoading,
  targetUnits,
  allowedByUnit,
  fullCatalog,
  needsApproval,
  grantableWorkspaces,
  initialProvider = null,
  mode = "org",
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
  /** Pre-selected vendor slug. Set from a provider's own screen, where the
   *  answer is the page you are on; null from the list, where it is a question. */
  initialProvider?: string | null;
  /**
   * "org" (default): today's onboarding shape — key optional, verified only
   * after Save. "bu-add-key": a BU Admin adding their own key to a provider
   * their org already granted (spec §5) — the key is REQUIRED and must pass
   * a live "Test" before Save enables, rather than being verified afterward.
   * Independent of `initialProvider`: reached from the page's single "Add
   * key" button, the provider combobox stays live (just narrowed to granted
   * providers via `catalog`) rather than locked to one tile's answer.
   */
  mode?: "org" | "bu-add-key";
  onAdded: () => void;
}) {
  const [provider, setProvider] = React.useState<ModelProviderKind | "">(initialProvider ?? "");
  const [providerOpen, setProviderOpen] = React.useState(false);
  const [displayName, setDisplayName] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [apiBase, setApiBase] = React.useState("");
  // bu-add-key only: the pre-save "Test" button's result. Gates Save — a key
  // that has never been proven to work must not be the one that gets stored.
  const [testStatus, setTestStatus] = React.useState<"idle" | "testing" | "valid" | "invalid">(
    "idle",
  );
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

  /** True when the vendor came from the page rather than from the picker. */
  const providerLocked = !!initialProvider;
  const isBuAddKey = mode === "bu-add-key";

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
      // Back to the pre-selected vendor, not to blank: reopening from a
      // provider's screen must land on that provider again.
      setProvider(initialProvider ?? "");
      setProviderOpen(false);
      setDisplayName("");
      setApiKey("");
      setApiBase("");
      setTestStatus("idle");
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
  }, [open, initialProvider]);

  // `provider` (state) only tracked `initialProvider` (prop) via the
  // close-time reset above — fine when the dialog unmounts between uses, but
  // this instance is long-lived (page.tsx renders it once and toggles `open`
  // by changing `initialProvider`), so the FIRST open ever, or an open
  // whose `initialProvider` changed since the last close (e.g. "Add key"
  // clicked on a different row before the dialog was ever closed), left
  // `provider` stuck at its stale/initial value. `providerLocked`'s own text
  // still read correctly (it's derived straight from the prop), so Step 1
  // looked right while every step after it — gated on `provider` — silently
  // never rendered. Keep it synced continuously whenever locked.
  React.useEffect(() => {
    if (initialProvider) setProvider(initialProvider);
  }, [initialProvider]);

  // A passing Test is a claim about ONE exact (key, base) pair. Edit either
  // afterward and the claim is stale — re-idle rather than let a proven-good
  // result silently vouch for a key that was never actually tested.
  React.useEffect(() => {
    setTestStatus("idle");
    // apiKey/apiBase are exactly what a Test result is a claim about — no
    // other identifier is read here, so there's nothing else for the deps
    // array to name.
  }, [apiKey, apiBase]);

  // Switching target unit invalidates the model picks: they were chosen from
  // the other unit's grants and may not exist in this one. A locked provider
  // survives it — the page still names the vendor either way.
  React.useEffect(() => {
    if (!providerLocked) setProvider("");
    setEnabled({});
    setModelQuery("");
  }, [targetUnitId, providerLocked]);

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

  // Whichever model got chosen first is what "Test" probes with — the models
  // step always comes before the credential step, so by the time a key can be
  // typed at all, this is never empty in bu-add-key mode (Credential doesn't
  // render until `hasModels`).
  const probeModel = enabledModels[0] ?? validCustomModels[0]?.model_id ?? null;

  // THE KEY IS OPTIONAL — except in bu-add-key mode, where it is the entire
  // point of the dialog (spec §5): a BU Admin opened this specifically to
  // bring their own credential, so an empty key here isn't a deferred choice
  // the way it is for an Org Admin registering a provider centrally, it's an
  // incomplete one.
  const sharedValid = displayName.trim().length > 0;
  const endpoint = provider ? ENDPOINT_REQUIRED[provider] : undefined;
  const endpointValid = !endpoint || apiBase.trim().length > 0;
  const keyValid = !isBuAddKey || apiKey.trim().length > 0;
  // A key that has never been proven to work must not be the one that gets
  // stored — Save stays closed until "Test" has said so for THIS key.
  const testPassed = !isBuAddKey || testStatus === "valid";
  // Reach is genuinely optional HERE, and only here: onboarding a provider and
  // deciding who may use it are two decisions, and an admin who holds the key
  // but not yet the answer must still be able to save. Editing an existing
  // grant is the opposite case — emptying it there means "revoke everywhere",
  // which is a thing to warn about rather than to wave through.
  const targetValid = !targetUnits || !!resolvedTargetId;
  const canSubmit =
    !!provider && hasModels && sharedValid && endpointValid && keyValid && testPassed && targetValid;

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

  // bu-add-key only: proves the key before anything is created with it. Uses
  // the same live 1-token probe `verifyModelProvider` runs post-save, but
  // stateless — no `model_providers` row exists yet for it to check.
  const handleTest = async () => {
    if (!provider || !apiKey.trim() || !probeModel || testStatus === "testing") return;
    setTestStatus("testing");
    try {
      const result = await probeModelProvider({
        provider,
        api_key: apiKey,
        api_base: apiBase.trim() || undefined,
        model: probeModel,
      });
      setTestStatus(result.status === "valid" ? "valid" : "invalid");
      if (result.status !== "valid") {
        toast.error("Key rejected — verification failed");
      }
    } catch (err) {
      setTestStatus("invalid");
      toast.error("Couldn't test this key", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

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
        toast.success(providerLocked ? "Models added" : "Provider registered", {
          description: `${created.display_name} has no key yet — its models can be granted, but stay inert until one is added.`,
        });
      } else {
        const result = await verifyModelProvider(created.id);
        if (result.status === "valid") {
          toast.success(providerLocked ? "Models added — key verified ✓" : "Provider verified ✓");
        } else {
          toast.error("Key rejected — verification failed");
        }
      }
      onAdded();
      onOpenChange(false);
    } catch (err) {
      toast.error(providerLocked ? "Couldn't add models" : "Couldn't add provider", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPending(false);
    }
  };

  const lockedLabel = initialProvider
    ? (activeCatalog.find((c) => c.provider === initialProvider)?.label ??
      providerLabel(initialProvider))
    : null;

  return (
    <Dialog open={open} onOpenChange={(v) => !pending && onOpenChange(v)}>
      <DialogContent className="max-h-[92vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-display">
            {isBuAddKey ? `Add a key for ${lockedLabel}` : providerLocked ? `Add a model to ${lockedLabel}` : "Add model provider"}
          </DialogTitle>
          <DialogDescription>
            {/* The provider step is a question from the list and a statement
                from a provider's own screen, so the instruction must not keep
                telling you to pick something already picked. */}
            {isBuAddKey
              ? "Choose the models, then the key that carries them. The key is required and must pass Test before this can be saved — it's stored in the tenant's secrets vault and never shown again."
              : providerLocked
                ? "Choose the models, then the key that carries them. Any key you give is stored in the tenant's secrets vault and never shown again, and we run a 1-token live probe to verify it on save."
                : needsApproval
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
              because every other answer depends on it — unless the page you
              came from already answered it. */}
          <Step
            n={STEP.provider}
            title="Provider"
            hint={
              providerLocked
                ? "Taken from the provider you're on — the models below are its own."
                : "Any provider LiteLLM supports — Anthropic, OpenAI, Azure OpenAI, AWS Bedrock, Google Vertex, xAI. Pick one to see its models with pricing."
            }
          >
            {providerLocked ? (
              /* A statement, not a control. A disabled combobox showing the
                 right answer still reads as something you failed to be allowed
                 to change; there is nothing to change here, so there is no
                 control. */
              <div className="border-line-soft bg-surface-1 flex items-center gap-2.5 rounded-md border px-3 py-2">
                <span className="text-[13px] font-medium">{lockedLabel}</span>
                <span className="text-muted-foreground ml-auto font-mono text-[10.5px]">
                  {initialProvider}
                </span>
              </div>
            ) : (
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
            )}
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
                    {isBuAddKey ? "(required)" : "(optional)"}
                  </span>
                </Label>
                <div className="flex gap-2">
                  <Input
                    id="api-key"
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={
                      isBuAddKey ? "sk-…" : "sk-… — leave blank to let each unit bring its own"
                    }
                    autoComplete="off"
                    aria-invalid={isBuAddKey && testStatus === "invalid"}
                    className="flex-1"
                  />
                  {/* A separate, explicit affordance rather than folding this into
                      Save: Save commits (creates the row, writes the secret) and
                      Test must not — this key may still be wrong, and a wrong key
                      should be cheap to find out. */}
                  {isBuAddKey && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={handleTest}
                      disabled={!apiKey.trim() || !probeModel || testStatus === "testing" || pending}
                      className="border-line-soft shrink-0"
                    >
                      {testStatus === "testing" ? (
                        <Loader2 className="size-4 animate-spin" aria-hidden />
                      ) : null}
                      Test
                    </Button>
                  )}
                </div>
                {isBuAddKey ? (
                  <p
                    className={cn(
                      "text-[11.5px]",
                      testStatus === "valid"
                        ? "text-success"
                        : testStatus === "invalid"
                          ? "text-warning"
                          : "text-muted-foreground",
                    )}
                  >
                    {testStatus === "testing"
                      ? "Testing…"
                      : testStatus === "valid"
                        ? "Key verified — this can now be saved."
                        : testStatus === "invalid"
                          ? "Key rejected — check it and test again."
                          : "This business unit's own key. Test it before Save enables."}
                  </p>
                ) : (
                  <p className="text-muted-foreground text-[11.5px]">
                    {apiKey.trim().length > 0
                      ? "The platform pays for these models — no business unit needs a key."
                      : "Registers the provider without a key: its models can be granted, but stay inert until a business unit onboards its own."}
                  </p>
                )}
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
            {/* bu-add-key already proved the key via the standalone Test button
                above, so this button just commits it — "Add key" names the act,
                not a probe it isn't running here. A save with no key elsewhere
                runs no probe either, so it must not promise a test. */}
            {isBuAddKey
              ? pending
                ? "Adding…"
                : "Add key"
              : pending
                ? apiKey.trim()
                  ? "Testing…"
                  : "Saving…"
                : apiKey.trim()
                  ? "Test & Save"
                  : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
