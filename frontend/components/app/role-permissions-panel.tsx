"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronRight, Lock, RotateCcw } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { listRolePermissions, saveRolePermissions } from "@/lib/api/role-permissions";
import { usePermissionCatalog } from "@/hooks/use-permission-catalog";
import { ROLE_META, type PlatformRole } from "@/lib/roles";

/**
 * What each role can DO — the permission behind every button.
 *
 * The screen already said what a role owns and where its approvals route, but
 * never which permissions it holds. "Can a Developer disconnect Jira?" had no
 * answer here, only in `role-permissions.ts`. Read-only for everyone, because
 * the question is one you ask about your OWN role: a permission model you have
 * to be told rather than look up is one people work around.
 *
 * Editable by the Organization Admin, against the platform's shipped defaults
 * rather than in place of them — a changed role says so and can be put back.
 */
export function RolePermissionsPanel({ canEdit }: { canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState<PlatformRole | null>(null);

  /**
   * Ticks are STAGED, not saved.
   *
   * Saving on every checkbox turned "give the Developer three permissions"
   * into three writes and three toasts, each one live the instant it landed —
   * and a mis-click was a permission change already in force. The draft holds
   * the whole edit until Update, so a role changes once, deliberately, and
   * Discard is a real way out.
   *
   * Null means "no edit in progress" rather than a copy of the server's
   * answer: that distinction is what lets a refetch land without silently
   * overwriting what someone is mid-way through typing.
   */
  const [draft, setDraft] = React.useState<{ role: PlatformRole; permissions: string[] } | null>(
    null,
  );

  const rowsQ = useQuery({
    queryKey: ["role-permissions", "list"],
    queryFn: () => listRolePermissions(),
  });

  // The grantable permissions as the DATABASE has them, grouped and labelled by the
  // static catalogue. A permission added to `permissions` appears here without a
  // deploy; one the catalogue names but the database lacks is not offered, because
  // nothing could grant it. See hooks/use-permission-catalog.ts.
  const catalog = usePermissionCatalog();

  const save = useMutation({
    mutationFn: saveRolePermissions,
    onSuccess: (_r, vars) => {
      toast.success(
        vars.reset
          ? `${ROLE_META[vars.role as PlatformRole]?.label ?? vars.role} reset to its defaults`
          : `${ROLE_META[vars.role as PlatformRole]?.label ?? vars.role} updated`,
      );
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ["role-permissions"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (rowsQ.isLoading) return <LoadingState variant="list" rows={4} />;

  const rows = rowsQ.data ?? [];
  // `admin:*` is the wildcard every check falls back to. Rendering it as one
  // unticked box among forty would read as "holds almost nothing".
  const holdsWildcard = (perms: string[]) => perms.includes("admin:*");

  return (
    <div className="space-y-2">
      {rows.map((row) => {
        const role = row.role as PlatformRole;
        const meta = ROLE_META[role];
        if (!meta) return null;
        const expanded = open === role;
        const wildcard = holdsWildcard(row.effective);
        // The draft wins while one is open for THIS role; everything else
        // reads the server's answer.
        const editing = draft?.role === role ? draft.permissions : null;
        const shown = editing ?? row.effective;
        const dirty =
          editing !== null &&
          (editing.length !== row.effective.length ||
            editing.some((p) => !row.effective.includes(p)));

        return (
          <Card key={row.role} className="border-line-soft bg-panel-elevated overflow-hidden">
            <button
              type="button"
              onClick={() => {
                // Collapsing abandons an unsaved edit rather than keeping it
                // invisibly alive behind a closed row.
                if (draft?.role === role) setDraft(null);
                setOpen(expanded ? null : role);
              }}
              aria-expanded={expanded}
              aria-label={`${expanded ? "Hide" : "Show"} permissions for ${meta.label}`}
              className="hover:bg-surface-1/60 flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors"
            >
              <ChevronRight
                className={cn("size-4 shrink-0 transition-transform", expanded && "rotate-90")}
                aria-hidden
              />
              <span className="text-[13.5px] font-medium">{meta.label}</span>
              {row.overridden && (
                <Badge variant="outline" className="border-warning/50 text-warning font-mono text-[10px]">
                  Modified
                </Badge>
              )}
              {!row.editable && (
                <Badge variant="outline" className="text-muted-foreground gap-1 font-mono text-[10px]">
                  <Lock className="size-3" aria-hidden />
                  Fixed
                </Badge>
              )}
              {dirty && (
                <Badge variant="outline" className="border-brand-bright/50 text-brand-bright font-mono text-[10px]">
                  Unsaved
                </Badge>
              )}
              <span className="text-muted-foreground ml-auto shrink-0 font-mono text-[11px]">
                {wildcard ? "every permission" : `${shown.length} permissions`}
              </span>
            </button>

            {expanded && (
              <div className="border-line-soft space-y-4 border-t px-4 py-3">
                {wildcard ? (
                  <p className="text-muted-foreground text-[12.5px]">
                    Holds <span className="font-mono">admin:*</span> — the wildcard every
                    permission check falls back to. Editing it could lock the last administrator
                    out of the screen that would undo the change.
                  </p>
                ) : (
                  <>
                    {catalog.map((group) => (
                      <div key={group.group} className="space-y-1.5">
                        <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                          {group.group}
                        </p>
                        <ul className="grid gap-1.5 sm:grid-cols-2">
                          {group.perms.map((perm) => {
                            const held = shown.includes(perm.id);
                            const id = `${row.role}-${perm.id}`;
                            return (
                              <li key={perm.id} className="flex items-start gap-2">
                                <Checkbox
                                  id={id}
                                  checked={held}
                                  disabled={!canEdit || !row.editable || save.isPending}
                                  onCheckedChange={() =>
                                    setDraft({
                                      role,
                                      permissions: held
                                        ? shown.filter((p) => p !== perm.id)
                                        : [...shown, perm.id],
                                    })
                                  }
                                  className="mt-0.5"
                                />
                                <Label
                                  htmlFor={id}
                                  className={cn(
                                    "cursor-pointer text-[12px] leading-snug font-normal",
                                    !held && "text-muted-foreground",
                                  )}
                                >
                                  {perm.label}
                                  <span className="text-muted-foreground/70 ml-1 font-mono text-[10.5px]">
                                    {perm.id}
                                  </span>
                                </Label>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ))}

                    {canEdit && row.editable && (
                      <div className="border-line-soft flex flex-wrap items-center gap-2 border-t pt-3">
                        <Button
                          size="sm"
                          disabled={!dirty || save.isPending}
                          onClick={() => save.mutate({ role: row.role, permissions: shown })}
                          className="h-7 px-2.5 text-[11.5px]"
                        >
                          {save.isPending ? "Updating…" : "Update role"}
                        </Button>
                        {dirty && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setDraft(null)}
                            className="text-muted-foreground h-7 px-2 text-[11.5px]"
                          >
                            Discard
                          </Button>
                        )}
                        {row.overridden && !dirty && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={save.isPending}
                            onClick={() => save.mutate({ role: row.role, reset: true })}
                            className="border-line-soft h-7 gap-1.5 px-2 text-[11.5px]"
                          >
                            <RotateCcw className="size-3" aria-hidden />
                            Reset to defaults
                          </Button>
                        )}
                        {dirty && (
                          <span className="text-muted-foreground text-[11.5px]">
                            Nothing changes until you update.
                          </span>
                        )}
                      </div>
                    )}
                    {!canEdit && (
                      <p className="text-muted-foreground text-[11.5px]">
                        Only an Organization Admin can change what a role holds.
                      </p>
                    )}
                  </>
                )}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}
