"use client";

import * as React from "react";
import { toast } from "sonner";

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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createCustomRole, updateCustomRole, type CustomRole, type CustomRoleScope } from "@/lib/api/roles";
import { PERMISSION_CATALOG } from "@/lib/auth/permission-catalog";
import { PHASE_LABEL } from "@/lib/agents";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { Phase, type InvolvementLevel } from "@/lib/schemas";

const ALL_PHASES = Phase.options;

const INVOLVEMENT_LABEL: Record<InvolvementLevel, string> = {
  owner: "Owner (uses + approves)",
  primary: "Primary (uses + approves)",
  build: "Build (hands-on, approved by someone else)",
  requests: "Requests only",
  use: "Use only",
  none: "No access",
};

const SCOPE_LABEL: Record<CustomRoleScope, string> = {
  organization: "Organization",
  business_unit: BUSINESS_UNIT_LABEL,
  project: "Project",
};

/** Which assignment dropdowns each scope shows up in — surfaced in the picker
 *  so an admin picks the right one without needing to read docs. */
const SCOPE_HINT: Record<CustomRoleScope, string> = {
  organization: "Not offered in any assignment dropdown yet — reserved for org-tier use.",
  business_unit: `Offered when assigning someone's role in a ${BUSINESS_UNIT_LABEL.toLowerCase()}.`,
  project: "Offered when assigning someone's role on a project.",
};

export interface RoleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** When set, the dialog edits this role instead of creating a new one. */
  initialRole?: CustomRole | null;
  onSaved: () => void;
}

export function RoleDialog({ open, onOpenChange, initialRole, onSaved }: RoleDialogProps) {
  const editing = Boolean(initialRole);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [scope, setScope] = React.useState<CustomRoleScope>("project");
  const [perms, setPerms] = React.useState<Set<string>>(new Set());
  const [agentAccess, setAgentAccess] = React.useState<Partial<Record<Phase, InvolvementLevel>>>({});
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setName(initialRole?.name ?? "");
    setDescription(initialRole?.description ?? "");
    setScope(initialRole?.scope ?? "project");
    setPerms(new Set(initialRole?.permissions ?? []));
    setAgentAccess(initialRole?.agentAccess ?? {});
    setErr(null);
  }, [open, initialRole]);

  function toggle(id: string, on: boolean | string) {
    setPerms((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const nonNoneAccess = Object.fromEntries(
        Object.entries(agentAccess).filter(([, v]) => v && v !== "none"),
      );
      const payload = {
        name: name.trim(),
        description: description.trim() || undefined,
        permissions: [...perms],
        agentAccess: Object.keys(nonNoneAccess).length ? nonNoneAccess : undefined,
        scope,
      };
      if (editing && initialRole) {
        await updateCustomRole(initialRole.id, payload);
        toast.success(`Role "${name.trim()}" updated`);
      } else {
        await createCustomRole(payload);
        toast.success(`Role "${name.trim()}" created`);
      }
      onOpenChange(false);
      onSaved();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : `Could not ${editing ? "update" : "create"} role`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit custom role" : "Create custom role"}</DialogTitle>
            <DialogDescription>Pick a name and the permissions this role grants.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="role-name">Name</Label>
              <Input
                id="role-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Junior Dev"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="role-desc">Description (optional)</Label>
              <Input
                id="role-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="role-scope">Scope</Label>
              <Select value={scope} onValueChange={(v) => setScope(v as CustomRoleScope)}>
                <SelectTrigger id="role-scope">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(SCOPE_LABEL) as CustomRoleScope[]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {SCOPE_LABEL[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-[11px]">{SCOPE_HINT[scope]}</p>
            </div>
            <div className="space-y-3">
              {PERMISSION_CATALOG.map((g) => (
                <div key={g.group} className="space-y-1.5">
                  <p className="text-muted-foreground text-[11px] font-semibold tracking-wider uppercase">
                    {g.group}
                  </p>
                  <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                    {g.perms.map((p) => (
                      <label key={p.id} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={perms.has(p.id)}
                          onCheckedChange={(v) => toggle(p.id, v)}
                        />
                        <span>{p.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="space-y-1.5">
              <p className="text-muted-foreground text-[11px] font-semibold tracking-wider uppercase">
                Agent access
              </p>
              <p className="text-muted-foreground text-[12px]">
                What this role can do on each agent by default. Leave &ldquo;No access&rdquo; for
                phases it shouldn&apos;t touch — a project can still grant more access for just
                that one project later.
              </p>
              <div className="max-h-52 space-y-1.5 overflow-y-auto rounded-md border p-2">
                {ALL_PHASES.map((phase) => (
                  <div key={phase} className="flex items-center justify-between gap-2">
                    <span className="text-sm">{PHASE_LABEL[phase]}</span>
                    <Select
                      value={agentAccess[phase] ?? "none"}
                      onValueChange={(v) =>
                        setAgentAccess((prev) => ({ ...prev, [phase]: v as InvolvementLevel }))
                      }
                    >
                      <SelectTrigger className="h-8 w-56 text-[12px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(INVOLVEMENT_LABEL) as InvolvementLevel[]).map((level) => (
                          <SelectItem key={level} value={level}>
                            {INVOLVEMENT_LABEL[level]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ))}
              </div>
            </div>

            {err && <p className="text-destructive text-xs">{err}</p>}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={busy || !name.trim() || perms.size === 0}>
              {editing ? "Save changes" : "Create role"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
