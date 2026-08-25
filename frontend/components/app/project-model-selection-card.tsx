"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Building2, Check } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getProjectModelSelection, setProjectModelSelection } from "@/lib/api/models";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { ModelAllowEntry } from "@/lib/schemas/model";

const keyOf = (e: ModelAllowEntry) => `${e.provider}::${e.model_id}`;

/**
 * Which of the models pushed to this project is its "master" key (PRD §34.2,
 * design decision #4).
 *
 * This card used to let a Project Admin browse the whole set their Business
 * Unit had been granted and freely tick which ones the project runs on — genuine
 * self-service selection, straight out of `inherited`. That's gone: a
 * Business Unit Admin now decides which credentialed models actually reach a
 * project (`assignProviderToProject`, from the provider's own screen), and
 * `selected` is that pushed set, not a catalogue to shop from. The one thing
 * left for the Project Admin to decide is which of THOSE pushed entries is
 * the default — everything else here is read-only.
 *
 * A project that has never been assigned anything still resolves `selected`
 * to the whole inherited set server-side (`usingDefaults`), so this can show
 * models before any Business Unit Admin has explicitly pushed one — that is
 * shown as inheritance, never as a selection someone made, and there is still
 * no control here to add or remove from it.
 */
export function ProjectModelSelectionCard({
  projectId,
  canManage,
}: {
  projectId: string;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["model", "project-selection", projectId] as const;

  const selectionQ = useQuery({ queryKey, queryFn: () => getProjectModelSelection(projectId) });

  const [draftDefault, setDraftDefault] = React.useState<string | null>(null);

  const data = selectionQ.data;
  /**
   * Deduped on `keyOf`, because that key is the SELECTION IDENTITY here, not
   * just a React key.
   *
   * The same model can reach a project twice — pushed by two different
   * credentials, or once by the old self-service path and once by a Business
   * Unit Admin's push — and on a screen that says "which models does this
   * project use" they are one row, not two identical ones wired to the same
   * "make default" control.
   */
  const selected = React.useMemo(() => {
    const seen = new Set<string>();
    return (data?.selected ?? []).filter((e) => {
      const k = keyOf(e);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [data]);

  // Reset the draft whenever the server's answer changes, so a Business Unit
  // Admin pushing another key mid-edit doesn't leave a stale pick behind.
  const serverSignature = data
    ? `${selected.map(keyOf).sort().join(",")}|${data.defaultKey ?? ""}`
    : null;
  React.useEffect(() => {
    setDraftDefault(null);
  }, [serverSignature]);

  const saveM = useMutation({
    mutationFn: (input: { selected: ModelAllowEntry[]; defaultKey: string | null }) =>
      setProjectModelSelection(projectId, input),
    onSuccess: () => {
      toast.success("Default model updated");
      queryClient.invalidateQueries({ queryKey });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save default model"),
  });

  const defaultKey = draftDefault ?? data?.defaultKey ?? null;
  const dirty = draftDefault !== null;

  const save = () => {
    // `selected` itself is never edited here — only re-sent as-is, exactly as
    // the server returned it, since the PUT endpoint replaces the whole list
    // rather than merging. Picking a default must never accidentally drop or
    // add an entry a Business Unit Admin pushed.
    const entries = data?.selected ?? [];
    const resolvedDefault =
      defaultKey && selected.some((e) => keyOf(e) === defaultKey)
        ? defaultKey
        : (selected[0] ? keyOf(selected[0]) : null);
    saveM.mutate({ selected: entries, defaultKey: resolvedDefault });
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Models this project uses</CardTitle>
            <CardDescription>
              {data?.inheritedFrom ? (
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  <Building2 className="size-3.5 shrink-0" aria-hidden />
                  Inherited from{" "}
                  <span className="text-foreground font-medium">{data.inheritedFrom.name}</span>
                  {data.usingDefaults && (
                    <Badge variant="outline" className="font-mono text-[10px]">
                      using all inherited
                    </Badge>
                  )}
                </span>
              ) : (
                `This project has no ${BUSINESS_UNIT_LABEL.toLowerCase()}, so it inherits no models.`
              )}
            </CardDescription>
          </div>
          {canManage && dirty && (
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setDraftDefault(null)}
                disabled={saveM.isPending}
              >
                Cancel
              </Button>
              <Button size="sm" onClick={save} disabled={saveM.isPending}>
                {saveM.isPending ? "Saving…" : "Save"}
              </Button>
            </div>
          )}
        </div>
      </CardHeader>

      <CardContent>
        {selectionQ.isLoading ? (
          <p className="text-muted-foreground text-sm">Loading…</p>
        ) : selected.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Your {BUSINESS_UNIT_LABEL} Admin hasn&apos;t assigned this project any models yet. Ask
            them to push a key from a provider&apos;s own screen — a project can only use what its{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} Admin has explicitly given it.
          </p>
        ) : (
          <ul className="divide-y rounded-md border">
            {selected.map((e) => {
              const k = keyOf(e);
              const isDefault = defaultKey === k;
              return (
                <li key={k} className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
                  <div className="min-w-0 flex-1">
                    <span className="font-mono text-[12.5px]">{e.model_id}</span>
                    <span className="text-muted-foreground ml-2 text-[11.5px]">{e.provider}</span>
                    {e.credentialName && (
                      <span className="text-muted-foreground ml-2 text-[11px]">
                        via {e.credentialName}
                      </span>
                    )}
                  </div>

                  {isDefault ? (
                    <Badge className="gap-1 font-mono text-[10px]">
                      <Check className="size-3" aria-hidden />
                      default
                    </Badge>
                  ) : (
                    canManage && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground h-7 text-[11.5px]"
                        disabled={saveM.isPending}
                        onClick={() => setDraftDefault(k)}
                      >
                        Make default
                      </Button>
                    )
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {!canManage && selected.length > 0 && (
          <p className="text-muted-foreground mt-3 text-xs">
            Only a Project Admin can change which of these is the default.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
