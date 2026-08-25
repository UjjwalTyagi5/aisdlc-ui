"use client";

import * as React from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
  substringFilter,
} from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { addModelProvider } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import type { CatalogProvider } from "@/lib/schemas/model";

/**
 * Org Admin registers a provider — keylessly. This is the ONLY create action
 * left in the Org Admin flow (spec §2 amendment 5: "Org Admin never adds a
 * key, period") — it exists purely to put a card on the grid so the grant
 * toggle and model-curation dialog have something to act on, for a provider
 * nobody in this org has ever connected to at any scope yet.
 *
 * Calls the same `POST /model/providers` route the org-wide onboarding flow
 * always used, with `workspaceId: null` and no `api_key` — the backend has
 * always accepted a keyless org-wide registration (`create_provider`'s own
 * docstring: "api_key may be None/empty — the connection is registered with
 * no secret... so its models can be granted centrally while a Business Unit
 * or project supplies its own key later"). Nothing here is new capability;
 * this is the first Org-Admin-facing UI to call it since amendment 5 removed
 * the old combined provider+model+key dialog.
 */
export function AddProviderDialog({
  open,
  onOpenChange,
  catalog,
  existingProviderKinds,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalog: CatalogProvider[];
  /** Providers already on the grid — excluded from the picker so this can't
   *  create a second row for one that already exists. */
  existingProviderKinds: string[];
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = React.useState<CatalogProvider | null>(null);
  const [displayName, setDisplayName] = React.useState("");

  const existing = React.useMemo(() => new Set(existingProviderKinds), [existingProviderKinds]);
  const available = React.useMemo(
    () => catalog.filter((c) => !existing.has(c.provider)),
    [catalog, existing],
  );

  const reset = () => {
    setSelected(null);
    setDisplayName("");
  };

  const addM = useMutation({
    mutationFn: () =>
      addModelProvider({
        provider: selected!.provider,
        display_name: displayName.trim(),
        api_key: "",
        workspaceId: null,
        // create_provider requires at least one model on any connection, even
        // a keyless one — but which of these a BU may actually USE is decided
        // entirely by org_model_grants (ProviderModelCurationDialog), keyed on
        // provider+model_id directly, never on this row's own offerings. So
        // there is no picker here: the whole catalogue for this provider goes
        // on as "technically supported", and curation narrows it afterward.
        enabled_models: selected!.models.map((m) => m.model_id),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["model", "providers"] });
      queryClient.invalidateQueries({ queryKey: qk.model.grantMatrix() });
      toast.success(`${selected!.label} added`, {
        description: "Grant it to a business unit and curate its models from its card.",
      });
      reset();
      onOpenChange(false);
    },
    onError: (err) => {
      toast.error("Couldn't add provider", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add provider</DialogTitle>
          <DialogDescription>
            Registers the provider so you can grant it to business units and curate its
            models. No credential is collected here — each business unit brings its own
            key when it adds one.
          </DialogDescription>
        </DialogHeader>

        {!selected ? (
          <Command filter={substringFilter}>
            <CommandInput placeholder="Search providers…" />
            <CommandList className="max-h-[min(50vh,20rem)]">
              <CommandEmpty>
                {available.length === 0
                  ? "Every catalogue provider is already added."
                  : "No matching provider."}
              </CommandEmpty>
              {available.map((c) => (
                <CommandItem
                  key={c.provider}
                  value={`${c.label} ${c.provider}`}
                  onSelect={() => {
                    setSelected(c);
                    setDisplayName(c.label);
                  }}
                >
                  <span className="truncate text-[13px]">{c.label}</span>
                </CommandItem>
              ))}
            </CommandList>
          </Command>
        ) : (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="add-provider-name">Subscription name</Label>
              <Input
                id="add-provider-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={selected.label}
              />
            </div>
            {selected.models.length === 0 && (
              <p className="text-warning text-[12px]">
                {selected.label} has no catalogued models yet, so it can&apos;t be added.
              </p>
            )}
            <DialogFooter className="gap-2 sm:gap-2">
              <Button type="button" variant="outline" onClick={() => setSelected(null)}>
                Back
              </Button>
              <Button
                type="button"
                disabled={!displayName.trim() || selected.models.length === 0 || addM.isPending}
                onClick={() => addM.mutate()}
              >
                {addM.isPending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                Add provider
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
