"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Building2, Check } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
 * Which of its inherited models a project actually uses (PRD §34.2).
 *
 * Everything above this card is governance — the Org Admin decides what the
 * organization may use, the {BUSINESS_UNIT_LABEL} Admin narrows that to what
 * their unit may use. Neither of them knows which model suits this team's
 * work, so the last narrowing belongs to the Project Admin, and only the last
 * one. This card can therefore never *add* a model: everything it offers came
 * down the cascade, and a model the unit revokes disappears from here whether
 * the project had selected it or not.
 *
 * A project that has never touched this inherits the whole allowed set. That
 * is shown as inheritance, not as a selection someone made — the distinction
 * matters when a {BUSINESS_UNIT_LABEL} Admin later widens the list and the
 * project should pick the new model up automatically.
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

  const [draft, setDraft] = React.useState<Set<string> | null>(null);
  const [draftDefault, setDraftDefault] = React.useState<string | null>(null);

  // Reset the draft whenever the server's answer changes, so a BU Admin
  // widening the allow-list mid-edit doesn't leave a stale checkbox set.
  const serverSignature = selectionQ.data
    ? `${selectionQ.data.selected.map(keyOf).sort().join(",")}|${selectionQ.data.defaultKey ?? ""}`
    : null;
  React.useEffect(() => {
    setDraft(null);
    setDraftDefault(null);
  }, [serverSignature]);

  const saveM = useMutation({
    mutationFn: (input: { selected: ModelAllowEntry[]; defaultKey: string | null }) =>
      setProjectModelSelection(projectId, input),
    onSuccess: () => {
      toast.success("Project models updated");
      queryClient.invalidateQueries({ queryKey });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save model selection"),
  });

  const data = selectionQ.data;
  const inherited = data?.inherited ?? [];
  const serverSelected = React.useMemo(
    () => new Set((data?.selected ?? []).map(keyOf)),
    [data],
  );

  const selected = draft ?? serverSelected;
  const defaultKey = draftDefault ?? data?.defaultKey ?? null;
  const dirty = draft !== null || draftDefault !== null;

  const toggle = (k: string, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(k);
    else next.delete(k);
    setDraft(next);
    // Dropping the default model has to promote something, or the project
    // resolves to nothing on its next run.
    if (!on && defaultKey === k) setDraftDefault([...next][0] ?? null);
  };

  const save = () => {
    const entries = inherited.filter((e) => selected.has(keyOf(e)));
    saveM.mutate({
      selected: entries,
      defaultKey: defaultKey && selected.has(defaultKey) ? defaultKey : (entries[0] ? keyOf(entries[0]) : null),
    });
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
                onClick={() => {
                  setDraft(null);
                  setDraftDefault(null);
                }}
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
        ) : inherited.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Your {BUSINESS_UNIT_LABEL} Admin hasn&apos;t allowed any models for this{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} yet. Ask them to add some — a project can only use
            what it inherits.
          </p>
        ) : (
          <ul className="divide-y rounded-md border">
            {inherited.map((e) => {
              const k = keyOf(e);
              const on = selected.has(k);
              const isDefault = defaultKey === k;
              return (
                <li
                  key={k}
                  className={cn(
                    "flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm",
                    !on && "opacity-60",
                  )}
                >
                  <Checkbox
                    id={`model-${k}`}
                    checked={on}
                    disabled={!canManage || saveM.isPending}
                    onCheckedChange={(c) => toggle(k, c === true)}
                    aria-label={`Use ${e.model_id} on this project`}
                  />
                  <label htmlFor={`model-${k}`} className="min-w-0 flex-1 cursor-pointer">
                    <span className="font-mono text-[12.5px]">{e.model_id}</span>
                    <span className="text-muted-foreground ml-2 text-[11.5px]">{e.provider}</span>
                  </label>

                  {isDefault ? (
                    <Badge className="gap-1 font-mono text-[10px]">
                      <Check className="size-3" aria-hidden />
                      default
                    </Badge>
                  ) : (
                    canManage &&
                    on && (
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

        {!canManage && inherited.length > 0 && (
          <p className="text-muted-foreground mt-3 text-xs">
            Only a Project Admin can change which of these the project uses.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
