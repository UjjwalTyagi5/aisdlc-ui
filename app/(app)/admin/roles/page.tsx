"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { RestrictedAccess } from "@/components/auth/restricted-access";
import { AccessHeader, AccessTabs } from "@/components/app/access-tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSession } from "@/hooks/use-session";
import { deleteCustomRole, listCustomRoles, type CustomRole } from "@/lib/api/roles";
import { hasPermission } from "@/lib/auth/permissions";

import { RoleDialog } from "./create-role-dialog";

export default function RolesPage() {
  const session = useSession({ required: true });
  const qc = useQueryClient();
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

  if (!hasPermission(session, "role:manage")) {
    return (
      <RestrictedAccess description="Managing roles requires the role:manage permission (org admin)." />
    );
  }

  const custom = customQ.data ?? [];

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <AccessHeader description="A custom role is a governed bundle: a named, reusable set of permissions and specific agent access. It can never include a permission its creator does not itself hold, and can never mix governance and delivery permissions in one bundle." />

      <AccessTabs />

      <div className="flex items-end justify-between gap-4">
        <p className="text-muted-foreground text-[12.5px]">
          Composed from the same catalog as the built-in roles, scoped
          organization, business unit or project — a role&apos;s scope decides
          which assignment dropdowns offer it.
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
            custom.map((r: CustomRole) => (
              <div
                key={r.id}
                className="flex items-start justify-between gap-3 border-b py-2 last:border-b-0"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium">{r.name}</p>
                    <Badge variant="outline" className="font-mono text-[10px] capitalize">
                      {r.scope.replace("_", " ")}
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
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <RoleDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initialRole={editingRole}
        onSaved={() => qc.invalidateQueries({ queryKey: ["custom-roles"] })}
      />
    </div>
  );
}
