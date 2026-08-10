"use client";

import { CheckCircle2, Network, Plug } from "lucide-react";

import { PwcMark } from "@/components/brand/pwc-mark";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Auth0SignInButton } from "@/app/(auth)/login/auth0-signin-button";
import { EmailPasswordForm } from "@/app/(auth)/login/email-password-form";
import { MockSignInPanel } from "@/app/(auth)/login/mock-signin-panel";
import { isLocalAuth, isMockAuth, isOidcEnabled } from "@/lib/auth/mode";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

const PROPS = [
  {
    icon: Network,
    title: "Thirteen agents, one control plane",
    text: "Requirements through Documentation, plus five track-specific agents for modernization, migration, and data engineering.",
  },
  {
    icon: CheckCircle2,
    title: "Enterprise-ready on day one",
    text: "SSO (OIDC), tenant isolation, immutable audit, cost & quality observability.",
  },
  {
    icon: Plug,
    title: "Plugs into your existing tools",
    text: "Azure DevOps, Jira, GitHub, Slack. No rip-and-replace.",
  },
];

/**
 * Sign-in as a large, translucent split popup on the marketing landing — a
 * restrained brand panel on the left, the sign-in card on the right. Reuses the
 * same EmailPasswordForm as /login, so behaviour (tier redirect, errors) matches.
 */
export function LoginDialog({
  open,
  onOpenChange,
  redirectTo = "/projects",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  redirectTo?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft/60 bg-panel-elevated/80 overflow-hidden p-0 shadow-2xl backdrop-blur-2xl sm:max-w-4xl lg:max-w-5xl">
        <div className="grid md:grid-cols-[1.05fr_1fr]">
          {/* Brand panel — restrained: a tinted glass surface with atmosphere,
              brand reserved for small accents (not a solid orange slab). */}
          <div className="border-line-soft/60 relative hidden flex-col justify-between gap-10 border-r p-10 md:flex">
            <div className="bg-mesh pointer-events-none absolute inset-0 opacity-70" aria-hidden />
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-px"
              aria-hidden
              style={{
                background:
                  "linear-gradient(90deg, transparent, color-mix(in oklab, var(--brand-bright) 60%, transparent), transparent)",
              }}
            />

            <div className="relative flex items-center gap-3">
              <PwcMark size={42} />
              <div className="flex flex-col leading-tight">
                <span className="font-display text-sm font-bold tracking-tight">SDLC Platform</span>
                <span className="text-brand-bright font-mono text-[10px] font-semibold tracking-[0.14em] uppercase">
                  Powered by PwC
                </span>
              </div>
            </div>

            <div className="relative space-y-6">
              <h2 className="font-display text-2xl leading-snug font-bold tracking-tight">
                Ship software through{" "}
                <span className="bg-brand-gradient bg-clip-text text-transparent">
                  coordinated AI agents
                </span>
                .
              </h2>
              <ul className="space-y-5">
                {PROPS.map((p) => (
                  <li key={p.title} className="flex items-start gap-3">
                    <span className="bg-brand-bright/12 text-brand-bright ring-brand-bright/15 mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg ring-1">
                      <p.icon className="size-4" aria-hidden />
                    </span>
                    <div className="space-y-0.5">
                      <p className="text-sm font-semibold">{p.title}</p>
                      <p className="text-muted-foreground text-xs">{p.text}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            <p className="text-muted-foreground relative font-mono text-[10px] tracking-wide uppercase">
              SOC 2 Type I · GDPR-ready · BYO-LLM-key
            </p>
          </div>

          {/* Sign-in panel */}
          <div className="flex flex-col justify-center gap-5 p-8 sm:p-10">
            <div className="flex items-center gap-3 md:hidden">
              <PwcMark size={40} />
              <span className="font-display text-sm font-bold tracking-tight">SDLC Platform</span>
            </div>
            <div className="space-y-1.5">
              <DialogTitle className="font-display text-2xl tracking-tight">
                {isLocalAuth
                  ? `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`
                  : isMockAuth || !isOidcEnabled
                    ? "Continue (mock mode)"
                    : `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
              </DialogTitle>
              <DialogDescription>
                {isLocalAuth
                  ? "Use the email and password set up by your administrator."
                  : isMockAuth || !isOidcEnabled
                    ? "Auth0 isn't configured — using a local session cookie. Pick a role to preview permissions."
                    : `Use your work account. Enterprise SSO is configured by your ${BUSINESS_UNIT_LABEL.toLowerCase()} admin.`}
              </DialogDescription>
            </div>
            {/* Mirrors the branching in app/(auth)/login/page.tsx so this popup and
                the dedicated /login route never disagree about which auth mode is live. */}
            {isLocalAuth ? (
              <EmailPasswordForm redirectTo={redirectTo} />
            ) : !isMockAuth && isOidcEnabled ? (
              <Auth0SignInButton redirectTo={redirectTo} />
            ) : (
              <MockSignInPanel redirectTo={redirectTo} />
            )}
            <p className="text-muted-foreground text-center text-xs">
              By continuing you agree to the{" "}
              <a href="#" className="underline underline-offset-2">
                terms of service
              </a>{" "}
              and{" "}
              <a href="#" className="underline underline-offset-2">
                privacy policy
              </a>
              .
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
