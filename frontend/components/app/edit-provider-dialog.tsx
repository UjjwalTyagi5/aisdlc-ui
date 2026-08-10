"use client";

import * as React from "react";
import { toast } from "sonner";
import { Loader2, Lock } from "lucide-react";

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
import { updateModelProvider } from "@/lib/api/models";
import type { CatalogProvider, ModelProvider } from "@/lib/schemas/model";

/**
 * Change a subscription: its name, its key, its endpoint, which models it
 * offers, and its limits.
 *
 * Lives here rather than inside the Models list page because BOTH screens need
 * it. The list page is where you notice a key is stale; the provider detail
 * screen is where you are when you realise the model a unit depends on runs on
 * the wrong contract — and having the dialog on only one of them meant an Org
 * Admin looking at a global model had no way to rotate its key from where they
 * were standing.
 */
export function EditProviderDialog({
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
  // Rotation only: the stored secret is never read back, so this starts empty
  // and an empty value means "leave it alone" rather than "clear it".
  const [newKey, setNewKey] = React.useState("");
  const [clearKey, setClearKey] = React.useState(false);
  const [apiBase, setApiBase] = React.useState("");
  const [rpm, setRpm] = React.useState("");
  const [tpm, setTpm] = React.useState("");
  const [costCap, setCostCap] = React.useState("");

  // Snapshot of the opened provider — used for dirty-checking and the default-model lock.
  // The org-wide default may only be disabled from the default radio, never from here,
  // so a disabled+default offering (which would block all runs) can never be produced.
  const original = React.useMemo(() => {
    if (!provider) return null;
    const enabledMap: Record<string, boolean> = {};
    for (const o of provider.offerings) if (o.enabled) enabledMap[o.model_id] = true;
    const lockedModelId =
      provider.offerings.find((o) => o.is_default && o.enabled)?.model_id ?? null;
    const first = provider.offerings.find((o) => o.enabled) ?? provider.offerings[0];
    return {
      displayName: provider.display_name,
      enabledKeys: Object.keys(enabledMap).sort(),
      lockedModelId,
      rpm: first?.rpm_limit != null ? String(first.rpm_limit) : "",
      tpm: first?.tpm_limit != null ? String(first.tpm_limit) : "",
      cost: first?.cost_limit_usd != null ? String(first.cost_limit_usd) : "",
    };
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
    setNewKey("");
    setClearKey(false);
    setApiBase(provider.api_base ?? "");
    // Limits are per-offering but set connection-wide, so the first enabled
    // offering is a faithful reading of the connection's current setting.
    const first = provider.offerings.find((o) => o.enabled) ?? provider.offerings[0];
    setRpm(first?.rpm_limit != null ? String(first.rpm_limit) : "");
    setTpm(first?.tpm_limit != null ? String(first.tpm_limit) : "");
    setCostCap(first?.cost_limit_usd != null ? String(first.cost_limit_usd) : "");
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
  const limitsDirty =
    !!provider &&
    (apiBase.trim() !== (provider.api_base ?? "") ||
      rpm !== (original?.rpm ?? "") ||
      tpm !== (original?.tpm ?? "") ||
      costCap !== (original?.cost ?? ""));
  const dirty =
    !!original &&
    (trimmedName !== original.displayName ||
      enabledModels.slice().sort().join(" ") !== original.enabledKeys.join(" ") ||
      newKey.trim().length > 0 ||
      clearKey ||
      limitsDirty);

  const canSubmit = trimmedName.length > 0 && enabledModels.length > 0 && dirty;
  const lockedModelId = original?.lockedModelId ?? null;

  const handleSubmit = async () => {
    if (!provider || !canSubmit || pending) return;
    setPending(true);
    try {
      const num = (v: string) => (v.trim() === "" ? null : Number(v));
      await updateModelProvider(provider.id, {
        display_name: trimmedName,
        enabled_models: enabledModels,
        // undefined = untouched. Only an explicit rotation or clear is sent,
        // so saving a rename never disturbs the stored secret.
        api_key: clearKey ? "" : newKey.trim() ? newKey.trim() : undefined,
        api_base: apiBase.trim() || null,
        rpm_limit: num(rpm),
        tpm_limit: num(tpm),
        cost_limit_usd: num(costCap),
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
            Rename this subscription, rotate its key, point it at a different endpoint, change
            which models it offers, or adjust its limits. The provider itself can&apos;t change
            &mdash; that would be a different subscription.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="edit-display-name">Display name</Label>
            <Input
              id="edit-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Anthropic — Payments enterprise"
            />
          </div>

          {/* ── Credential ─────────────────────────────────────────────────
              The stored secret is never read back, so this is rotation only:
              blank leaves it alone. "Remove the key" is offered explicitly
              because a connection with no key is a valid state, not a
              half-finished one — the models stay grantable and go inert. */}
          <div className="space-y-1.5">
            <Label htmlFor="edit-api-key">
              API key{" "}
              <span className="text-muted-foreground/60 font-normal normal-case">
                {provider?.hasKey ? "(leave blank to keep)" : "(none stored)"}
              </span>
            </Label>
            <Input
              id="edit-api-key"
              type="password"
              value={newKey}
              disabled={clearKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder={provider?.hasKey ? "Enter a new key to rotate" : "sk-…"}
              autoComplete="off"
            />
            {provider?.hasKey && (
              <label className="text-muted-foreground flex items-center gap-2 text-[11.5px]">
                <Checkbox
                  checked={clearKey}
                  onCheckedChange={(v) => {
                    setClearKey(v === true);
                    if (v === true) setNewKey("");
                  }}
                />
                Remove the key — units bring their own
              </label>
            )}
            {(newKey.trim().length > 0 || clearKey) && (
              <p className="text-warning text-[11.5px]">
                Changing the key resets this connection to unverified — test it after saving.
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="edit-api-base">
              API base{" "}
              <span className="text-muted-foreground/60 font-normal normal-case">(optional)</span>
            </Label>
            <Input
              id="edit-api-base"
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="https://your-gateway.internal/v1"
              autoComplete="off"
              className="font-mono"
            />
          </div>

          {/* ── Limits ─────────────────────────────────────────────────────
              Applied to every model on this subscription, which is how they
              were set at creation. Blank means no limit — an empty field and a
              zero are different answers and must not collapse. */}
          <div className="space-y-1.5">
            <Label>Usage limits</Label>
            <div className="grid grid-cols-3 gap-2">
              <Input
                aria-label="Requests per minute"
                inputMode="numeric"
                value={rpm}
                onChange={(e) => setRpm(e.target.value)}
                placeholder="RPM"
                className="font-mono text-[12px]"
              />
              <Input
                aria-label="Tokens per minute"
                inputMode="numeric"
                value={tpm}
                onChange={(e) => setTpm(e.target.value)}
                placeholder="TPM"
                className="font-mono text-[12px]"
              />
              <Input
                aria-label="Monthly cost cap in USD"
                inputMode="decimal"
                value={costCap}
                onChange={(e) => setCostCap(e.target.value)}
                placeholder="$ / month"
                className="font-mono text-[12px]"
              />
            </div>
            <p className="text-muted-foreground text-[11.5px]">
              Blank means no limit. Applied to every model on this subscription.
            </p>
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

