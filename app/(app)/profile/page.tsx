"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BadgeCheck,
  Link2,
  Link2Off,
  Mail,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Separator } from "@/components/ui/separator";
import { useSession } from "@/hooks/use-session";
import { listConnectors } from "@/lib/api/connectors";
import { qk } from "@/lib/api/query-keys";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { ROLE_META } from "@/lib/roles";
import { AGENT_OWNERSHIP } from "@/lib/roles";
import { PHASE_LABEL } from "@/lib/agents";
import type { Phase } from "@/lib/schemas";

/**
 * Profile — your personal settings and your linked external accounts
 * (PRD §36, §32.1).
 *
 * The linked-accounts section is the visible half of the platform's identity
 * model (PRD §12.2, §34.3): access flows top-down (an admin permits a
 * connector), but *identity* is per person, bottom-up — you sign in with your
 * own account and the agent then acts as you. No admin ever holds your
 * credentials, and revocation is simply unlinking.
 *
 * Every action here is Safe (PRD §36, "manage own account & links").
 */

const SYSTEM_LABEL: Record<string, string> = {
  jira: "Jira",
  azure_devops: "Azure DevOps",
  github: "GitHub",
  azure_repos: "Azure Repos",
  github_actions: "GitHub Actions",
  slack: "Slack",
  sso_okta: "Okta",
  sso_entra: "Microsoft Entra",
};

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-line-soft bg-panel-elevated rounded-xl border">
      <div className="border-line-soft border-b px-4 py-3">
        <h2 className="font-display text-[13px] font-semibold tracking-tight">
          {title}
        </h2>
        {description && (
          <p className="text-muted-foreground mt-1 text-[12px] leading-snug">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

export default function ProfilePage() {
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);

  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(),
    queryFn: () => listConnectors(),
  });

  // Connectors that act on a person's behalf are the ones you link an account
  // to. SSO providers are the front door, not a per-person connector link.
  const linkable = React.useMemo(
    () =>
      (connectorsQ.data ?? []).filter(
        (c) => !c.kind.startsWith("sso_") && c.installed,
      ),
    [connectorsQ.data],
  );

  const user = session?.user;

  // Which agents this role may chat with (PRD §14.8) — useful orientation on
  // your own profile, and it makes the ownership model legible.
  const ownedAgents = React.useMemo(() => {
    if (!role) return [];
    const map = AGENT_OWNERSHIP[role];
    return (Object.keys(map) as Phase[]).filter(
      (p) => map[p] === "owner" || map[p] === "primary" || map[p] === "build",
    );
  }, [role]);

  return (
    <div className="w-full max-w-4xl space-y-6 p-4 md:px-10 md:py-8">
      <header
        className="flex flex-col items-start gap-1"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
          <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
          You
        </div>
        <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
          Profile
        </h1>
        <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">
          Your personal settings and the external accounts agents act as on your
          behalf.
        </p>
      </header>

      {/* ── Identity ─────────────────────────────────────────────────────── */}
      <Section
        title="Identity"
        description="Your organisation sign-in is the source of truth. It cannot be edited here."
      >
        <div className="flex flex-wrap items-center gap-4 px-4 py-4">
          <span
            className="bg-brand-gradient text-primary-foreground flex size-14 shrink-0 items-center justify-center rounded-full font-mono text-[16px] font-semibold"
            aria-hidden
          >
            {user?.initials ?? "—"}
          </span>

          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-semibold">{user?.name ?? "—"}</div>
            <div className="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-[12.5px]">
              <Mail className="size-3.5" aria-hidden />
              {user?.email ?? "—"}
            </div>
          </div>

          {role && (
            <div className="text-right">
              <div className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
                Role
              </div>
              <div className="mt-1 flex items-center justify-end gap-1.5">
                {ROLE_META[role].tier === "governance" && (
                  <ShieldCheck className="text-brand-bright size-3.5" aria-hidden />
                )}
                <span className="text-[13px] font-medium">{ROLE_META[role].label}</span>
              </div>
              <div className="text-muted-foreground mt-0.5 font-mono text-[10.5px] tracking-wide uppercase">
                {ROLE_META[role].tier} tier
              </div>
            </div>
          )}
        </div>

        {role && (
          <>
            <Separator className="bg-line-soft" />
            <div className="px-4 py-3">
              <div className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
                What this role does
              </div>
              <p className="mt-1.5 text-[13px] leading-relaxed">
                {ROLE_META[role].oneLiner}
              </p>

              {ownedAgents.length > 0 ? (
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="text-muted-foreground mr-1 text-[11.5px]">
                    Agents you drive:
                  </span>
                  {ownedAgents.map((p) => (
                    <Badge
                      key={p}
                      variant="secondary"
                      className="font-mono text-[10.5px] font-normal"
                    >
                      {PHASE_LABEL[p]}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-muted-foreground mt-3 text-[12px]">
                  Governance roles have no agent access — you govern structure,
                  limits and visibility.
                </p>
              )}
            </div>
          </>
        )}
      </Section>

      {/* ── Linked accounts ──────────────────────────────────────────────── */}
      <Section
        title="Linked accounts"
        description="Agents act as you in these systems, so the external tool records you as the real author. No administrator holds these credentials — unlinking revokes the access immediately."
      >
        {connectorsQ.isLoading ? (
          <div className="p-4">
            <LoadingState variant="list" rows={3} />
          </div>
        ) : linkable.length === 0 ? (
          <div className="text-muted-foreground px-4 py-6 text-[13px]">
            No connectors are enabled on your projects yet. Once an admin
            registers one, you&apos;ll link your own account here.
          </div>
        ) : (
          <ul className="divide-line-soft divide-y">
            {linkable.map((c) => {
              const linked = Boolean(c.account);
              return (
                <li
                  key={c.id}
                  className="flex flex-wrap items-center gap-3 px-4 py-3"
                >
                  <span
                    className={cn(
                      "flex size-8 shrink-0 items-center justify-center rounded-md",
                      linked ? "bg-success/10" : "bg-surface-2",
                    )}
                  >
                    {linked ? (
                      <Link2 className="text-success size-4" aria-hidden />
                    ) : (
                      <Link2Off className="text-muted-foreground size-4" aria-hidden />
                    )}
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium">
                        {SYSTEM_LABEL[c.kind] ?? c.name}
                      </span>
                      {linked && (
                        <span className="text-success inline-flex items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                          <BadgeCheck className="size-3" aria-hidden />
                          Linked
                        </span>
                      )}
                      {c.health !== "healthy" && (
                        <span className="text-warning inline-flex items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                          <TriangleAlert className="size-3" aria-hidden />
                          {c.health}
                        </span>
                      )}
                    </span>
                    <span className="text-muted-foreground mt-0.5 block truncate text-[11.5px]">
                      {linked
                        ? `Acting as ${c.account}`
                        : "Not linked — agent actions needing this system will pause until you sign in."}
                    </span>
                  </span>

                  <Button
                    variant={linked ? "outline" : "default"}
                    size="sm"
                    className="shrink-0"
                  >
                    {linked ? "Unlink" : "Link account"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      {/* ── Sign-in ──────────────────────────────────────────────────────── */}
      <Section
        title="Sign-in"
        description="Managed by your organisation's policy. Changes are made by an Organization Admin in Settings."
      >
        <dl className="divide-line-soft divide-y">
          <div className="flex items-center justify-between gap-4 px-4 py-3">
            <dt className="text-[13px]">Authentication</dt>
            <dd className="text-muted-foreground font-mono text-[12px]">
              {session?.mode === "auth0"
                ? "Single sign-on (Auth0)"
                : session?.mode === "local"
                  ? "Email and password"
                  : "Mock session"}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-4 px-4 py-3">
            <dt className="text-[13px]">Organisation</dt>
            <dd className="text-muted-foreground font-mono text-[12px]">
              {session?.tenant.name ?? "—"}
            </dd>
          </div>
        </dl>
      </Section>
    </div>
  );
}
