"use client";

import * as React from "react";
import { ShieldCheck, User, UserCog } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { Role } from "@/lib/auth/types";
import { ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";

const GOVERNANCE_ROLES = ROLE_ORDER.filter((r) => ROLE_META[r].tier === "governance");
const DELIVERY_ROLES = ROLE_ORDER.filter((r) => ROLE_META[r].tier === "delivery");

const ROLES: Array<{
  value: Role;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    value: "admin",
    label: "Admin",
    description: "Full access — settings, audit log, connectors, users.",
    icon: ShieldCheck,
  },
  {
    value: "member",
    label: "Member",
    description: "Create projects, trigger and approve runs.",
    icon: UserCog,
  },
  {
    value: "viewer",
    label: "Viewer",
    description: "Read-only access — no approvals, no settings.",
    icon: User,
  },
];

export function MockSignInPanel({ redirectTo }: { redirectTo: string }) {
  const [role, setRole] = React.useState<Role>("admin");
  const [platformRole, setPlatformRole] = React.useState<PlatformRole>("org_admin");
  // "platform" — the twelve real platform roles (Org Admin, BU Admin, Project
  // Admin, the eight contributors, Scrum Master) — is the default and
  // authoritative path (sets session.platformRole directly). "quick" is the
  // legacy 3-item admin/member/viewer picker from before the RBAC model was
  // introduced; it does NOT match the real hierarchy (there is no read-only
  // "Viewer" role) and is kept only so existing e2e specs that select by that
  // label keep working — it must never be the default a person signs in
  // through.
  const [mode, setMode] = React.useState<"quick" | "platform">("platform");

  return (
    <form action="/api/auth/mock/signin" method="POST" className="flex flex-col gap-4">
      <input type="hidden" name="from" value={redirectTo} />

      {mode === "quick" ? (
        <>
          <RadioGroup
            name="role"
            value={role}
            onValueChange={(v) => setRole(v as Role)}
            className="gap-2"
          >
            {ROLES.map((r) => (
              <Label
                key={r.value}
                htmlFor={`role-${r.value}`}
                className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors ${
                  role === r.value ? "border-primary bg-accent/40" : "hover:bg-accent/40"
                }`}
              >
                <RadioGroupItem value={r.value} id={`role-${r.value}`} className="mt-0.5" />
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <r.icon className="size-4" aria-hidden />
                    {r.label}
                  </div>
                  <p className="text-muted-foreground text-xs font-normal">{r.description}</p>
                </div>
              </Label>
            ))}
          </RadioGroup>

          <Button type="submit">Continue as {role}</Button>

          <button
            type="button"
            onClick={() => setMode("platform")}
            className="text-muted-foreground text-center text-xs underline underline-offset-2 hover:text-foreground"
          >
            ← Back to platform roles
          </button>
        </>
      ) : (
        <>
          <RadioGroup
            name="platformRole"
            value={platformRole}
            onValueChange={(v) => setPlatformRole(v as PlatformRole)}
            className="gap-2"
          >
            <p className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
              Governance
            </p>
            {GOVERNANCE_ROLES.map((r) => (
              <PlatformRoleOption key={r} role={r} selected={platformRole === r} />
            ))}
            <p className="text-muted-foreground mt-1 font-mono text-[10px] tracking-widest uppercase">
              Delivery
            </p>
            <div className="max-h-64 overflow-y-auto pr-1">
              {DELIVERY_ROLES.map((r) => (
                <PlatformRoleOption key={r} role={r} selected={platformRole === r} />
              ))}
            </div>
          </RadioGroup>

          <Button type="submit">Continue as {ROLE_META[platformRole].label}</Button>

          <button
            type="button"
            onClick={() => setMode("quick")}
            className="text-muted-foreground text-center text-xs underline underline-offset-2 hover:text-foreground"
          >
            Use the legacy quick picker instead →
          </button>
        </>
      )}
    </form>
  );
}

function PlatformRoleOption({ role, selected }: { role: PlatformRole; selected: boolean }) {
  const meta = ROLE_META[role];
  return (
    <Label
      htmlFor={`platform-role-${role}`}
      className={`mb-2 flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors ${
        selected ? "border-primary bg-accent/40" : "hover:bg-accent/40"
      }`}
    >
      <RadioGroupItem value={role} id={`platform-role-${role}`} className="mt-0.5" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="text-sm font-medium">{meta.label}</div>
        <p className="text-muted-foreground text-xs font-normal">{meta.oneLiner}</p>
      </div>
    </Label>
  );
}
