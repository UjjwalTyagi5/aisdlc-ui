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
import { RequestAccessButton } from "@/components/requests/request-access-button";
import type { ModelAllowEntry } from "@/lib/schemas/model";

const keyOf = (e: ModelAllowEntry) => `${e.provider}::${e.model_id}`;

/**
 * Which of its inherited models a project actually uses (PRD §34.2).
 *
 * Everything above this card is governance — the Org Admin decides which
 * models the organization may use and which {BUSINESS_UNIT_LABEL}s get each
 * one. They don't know which model suits this team's work, so the last
 * narrowing belongs to the Project Admin, and only the last one. This card can
 * therefore never *add* a model: everything it offers came down the cascade,
 * and a model the Org Admin revokes disappears from here whether the project
 * had selected it or not.
 *
 * A project that has never touched this inherits the whole granted set. That
 * is shown as inheritance, not as a selection someone made — the distinction
 * matters when the Org Admin later grants another model and the project should
 * pick it up automatically.
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

  // Reset the draft whenever the server's answer changes, so an Org Admin
  // granting another model mid-edit doesn't leave a stale checkbox set.
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
  /**
   * Deduped on `keyOf`, because that key is the SELECTION IDENTITY here, not
   * just a React key.
   *
   * The same model reaches a unit more than once when it is granted both
   * org-wide and to that unit specifically — two grant rows, one model. On a
   * screen that says "which models may this project use" they are one choice,
   * and rendering both produced two identical rows wired to the same
   * checkbox: ticking either appeared to tick both, and React warned about
   * the duplicate key on top.
   *
   * Making the key unique instead would have been the wrong fix — it would
   * have silenced the warning and left the two indistinguishable rows.
   */
  const inherited = React.useMemo(() => {
    const seen = new Set<string>();
    return (data?.inherited ?? []).filter((e) => {
      const k = keyOf(e);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [data]);
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
            Your Organization Admin hasn&apos;t granted this{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} any models yet. Ask them to grant some — a project
            can only use what it inherits.
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
                  ) : canManage ? (
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
                  ) : (
                    // The contributor's half of this row. A model the unit
                    // holds but the project has not turned on is the one thing
                    // they can actually do something about — and the person
                    // who grants it is the Project Admin, one tick away on
                    // this very card. `model_credential` tier-routes there.
                    !on && (
                      <RequestAccessButton
                        label="Request access"
                        className="h-7"
                        prefill={{
                          type: "model_credential",
                          title: `${e.model_id} on this project`,
                          description: `Requesting ${e.model_id} (${e.provider}). It is granted to the ${BUSINESS_UNIT_LABEL.toLowerCase()} but not turned on for this project.`,
                          projectId,
                        }}
                      />
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
