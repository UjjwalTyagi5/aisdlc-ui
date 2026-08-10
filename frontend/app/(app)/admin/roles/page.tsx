"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { RestrictedAccess } from "@/components/auth/restricted-access";
import { AccessHeader, AccessTabs } from "@/components/app/access-tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { deleteCustomRole, listCustomRoles, type CustomRole } from "@/lib/api/roles";
import { hasPermission } from "@/lib/auth/permissions";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

import { RoleDialog } from "./create-role-dialog";

/**
 * Custom roles — the Business Unit Admin's answer to "none of the built-in
 * roles fit this person".
 *
 * OWNED, NOT GLOBAL. A role composed here belongs to the unit that composed it
 * and is offered only when assigning inside that unit; the org-wide ones (no
 * owner) are the Organization Admin's and every unit may assign them. Both are
 * listed to everyone who manages roles, because an unfamiliar role name on a
 * colleague's row in the directory has to be explainable — but only the owner
 * can change what it grants.
 */
export default function RolesPage() {
  const session = useSession({ required: true });
  const qc = useQueryClient();
  const { isOrgWide, managedBusinessUnitIds } = useAccessScope();
  const workspacesQ = useWorkspaces();
  const customQ = useQuery({ queryKey: ["custom-roles"], queryFn: listCustomRoles });
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editingRole, setEditingRole] = React.useState<CustomRole | null>(null);

  const delM = useMutation({
    mutationFn: (id: string) => deleteCustomRole(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["custom-roles"] });
      toast.success("Role deleted");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not delete role"),
  });

  const unitName = React.useCallback(
    (id: string) => (workspacesQ.data ?? []).find((w) => String(w.id) === id)?.displayName ?? id,
    [workspacesQ.data],
  );

  /** Mirrors the server's `canWriteCustomRole`. An org-wide role is the
   *  Organization Admin's: a unit admin assigns it but must not rewrite what it
   *  grants in every other unit. */
  const canWrite = React.useCallback(
    (role: CustomRole) =>
      isOrgWide ||
      (role.businessUnitId !== null && managedBusinessUnitIds.includes(role.businessUnitId)),
    [isOrgWide, managedBusinessUnitIds],
  );

  // `role:manage` — Organization Admin AND Business Unit Admin. Gating this on
  // the Org Admin left a unit admin able to be told "compose a role" by the
  // assignment dialog and then refused by the screen that composes them.
  if (!hasPermission(session, "role:manage")) {
    return (
      <RestrictedAccess description="Composing roles requires the role:manage permission — an Organization or Business Unit Admin." />
    );
  }

  const custom = customQ.data ?? [];
  const soleUnit = !isOrgWide && managedBusinessUnitIds.length === 1 ? managedBusinessUnitIds[0]! : null;

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <AccessHeader description="A custom role is a governed bundle: a named, reusable set of permissions and specific agent access. It can never include a permission its creator does not itself hold, and can never mix governance and delivery permissions in one bundle." />

      <AccessTabs />

      <div className="flex items-end justify-between gap-4">
        <p className="text-muted-foreground text-[12.5px]">
          {isOrgWide
            ? `Composed from the same catalog as the built-in roles. A role you leave unowned is offered in every ${BUSINESS_UNIT_LABEL.toLowerCase()}; one you pin to a unit is offered only there.`
            : `Roles you compose belong to your ${BUSINESS_UNIT_LABEL.toLowerCase()} and are offered when you assign someone a role in it. Org-wide roles are listed here too, and are the Organization Admin's to change.`}
        </p>
        <Button
          size="sm"
          className="gap-1.5"
          onClick={() => {
            setEditingRole(null);
            setDialogOpen(true);
          }}
        >
          <Plus className="size-4" aria-hidden />
          New role
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Custom roles</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {custom.length === 0 ? (
            <p className="text-muted-foreground text-xs">
              No custom roles yet. Create one to grant a tailored set of permissions.
            </p>
          ) : (
            custom.map((r: CustomRole) => {
              const writable = canWrite(r);
              return (
                <div
                  key={r.id}
                  className="flex items-start justify-between gap-3 border-b py-2 last:border-b-0"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{r.name}</p>
                      <Badge variant="outline" className="font-mono text-[10px] capitalize">
                        {r.scope.replace("_", " ")}
                      </Badge>
                      {/* Whose role this is, said plainly — the difference
                          between "everyone gets this" and "Payments gets this"
                          is invisible otherwise. */}
                      <Badge
                        variant={r.businessUnitId ? "secondary" : "outline"}
                        className="font-mono text-[10px]"
                      >
                        {r.businessUnitId ? unitName(r.businessUnitId) : "org-wide"}
                      </Badge>
                    </div>
                    {r.description && (
                      <p className="text-muted-foreground text-xs">{r.description}</p>
                    )}
                    <div className="mt-1 flex flex-wrap gap-1">
                      {r.permissions.map((p) => (
                        <Badge key={p} variant="secondary" className="font-mono text-[10px]">
                          {p}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {writable ? (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setEditingRole(r);
                            setDialogOpen(true);
                          }}
                          aria-label={`Edit ${r.name}`}
                        >
                          <Pencil className="size-4" aria-hidden />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={delM.isPending}
                          onClick={() => delM.mutate(r.id)}
                          aria-label={`Delete ${r.name}`}
                        >
                          <Trash2 className="size-4" aria-hidden />
                        </Button>
                      </>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge
                            variant="outline"
                            className="text-muted-foreground gap-1 font-mono text-[9.5px]"
                          >
                            <Lock className="size-2.5" aria-hidden />
                            view only
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent side="left" className="max-w-[240px]">
                          {r.businessUnitId
                            ? `${unitName(r.businessUnitId)}'s admin owns this role.`
                            : "An org-wide role — the Organization Admin owns what it grants."}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      <RoleDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initialRole={editingRole}
        // A unit admin composes for their own unit and nowhere else, so the
        // scope question has one answer and is not worth asking.
        lockedScope={soleUnit ? "business_unit" : undefined}
        businessUnitId={soleUnit}
        businessUnitName={soleUnit ? unitName(soleUnit) : null}
        onSaved={() => qc.invalidateQueries({ queryKey: ["custom-roles"] })}
      />
    </div>
  );
}
