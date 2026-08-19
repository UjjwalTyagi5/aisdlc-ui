import type { Metadata } from "next";
import Link from "next/link";
import { CheckCircle2, Network, ShieldAlert, ShieldCheck, type LucideIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PwcMark } from "@/components/brand/pwc-mark";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isLocalAuth, isMockAuth, isOidcEnabled } from "@/lib/auth/mode";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

import { Auth0SignInButton } from "./auth0-signin-button";
import { EmailPasswordForm } from "./email-password-form";
import { MockSignInPanel } from "./mock-signin-panel";

export const metadata: Metadata = { title: "Sign in" };

const ERRORS = {
  invalid_session: {
    title: "Your session expired",
    message: "For security, we signed you out. Sign in again to pick up where you left off.",
  },
  tenant_not_found: {
    title: `${BUSINESS_UNIT_LABEL} not found`,
    message: `Your account doesn't belong to an active ${BUSINESS_UNIT_LABEL.toLowerCase()}. Ask your admin to invite you, or contact support.`,
  },
  sso_failed: {
    title: "SSO handshake failed",
    message:
      "Your identity provider rejected the login. Contact your admin to check SAML metadata + ACS URL.",
  },
} as const satisfies Record<string, { title: string; message: string }>;

function lookupError(code?: string): { title: string; message: string } | null {
  if (!code) return null;
  return code in ERRORS ? ERRORS[code as keyof typeof ERRORS] : null;
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ from?: string; error?: string }>;
}) {
  const { from, error } = await searchParams;
  // Dashboard is the default landing on sign-in (PRD §36).
  const redirectTo = from ?? "/dashboard";
  const err = lookupError(error);

  return (
    <main className="bg-background relative min-h-dvh">
      {/* Marketing-light hero strip — enterprise palette, subtle gradient, no floating particles */}
      <div
        aria-hidden
        className="from-primary/5 via-background to-background pointer-events-none absolute inset-x-0 top-0 -z-10 h-80 bg-gradient-to-b"
      />

      <div className="mx-auto grid min-h-dvh w-full max-w-6xl grid-cols-1 items-center gap-12 px-6 py-12 lg:grid-cols-[1fr_420px] lg:py-0">
        {/* Marketing column */}
        <section className="hidden space-y-8 lg:block" aria-label="Product summary">
          <div className="flex items-center gap-3">
            <PwcMark size={44} />
            <div className="flex flex-col leading-tight">
              <span className="text-lg font-semibold tracking-tight">SDLC Platform</span>
              <span className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
                Powered by PwC
              </span>
            </div>
          </div>

          <div className="space-y-3">
            <h1 className="text-4xl font-semibold tracking-tight">
              Ship features through coordinated AI agents.
            </h1>
            <p className="text-muted-foreground text-base">
              Requirements to deploy, with human approval at every phase, audited end-to-end,
              running inside your VPC.
            </p>
          </div>

          <ul className="space-y-3">
            <FeatureRow
              icon={Network}
              title="Thirteen agents, one control plane"
              blurb="Eight agents run Requirements through Documentation. Five more specialize for modernization, migration, and data engineering — picked up automatically by the delivery track you choose."
            />
            <FeatureRow
              icon={ShieldCheck}
              title="Enterprise-ready on day one"
              blurb="SSO (SAML / OIDC), tenant isolation, immutable audit, data residency."
            />
            <FeatureRow
              icon={CheckCircle2}
              title="Plugs into your existing tools"
              blurb="Jira, Azure DevOps, GitHub, Azure Repos, Slack. No rip-and-replace."
            />
          </ul>

          <div className="text-muted-foreground flex items-center gap-4 text-xs">
            <span>SOC 2 Type I · target GA</span>
            <span>·</span>
            <span>GDPR-ready</span>
            <span>·</span>
            <span>BYO-LLM-key</span>
          </div>
        </section>

        {/* Sign-in column */}
        <section className="mx-auto w-full max-w-md space-y-4" aria-label="Sign in">
          {/* Mobile brand mark — visible below lg */}
          <div className="flex flex-col items-center gap-2 pt-4 text-center lg:hidden">
            <PwcMark size={48} />
            <h1 className="text-2xl font-semibold tracking-tight">SDLC Platform</h1>
            <p className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
              Powered by PwC
            </p>
          </div>

          {err && (
            <Alert variant="destructive">
              <ShieldAlert />
              <AlertTitle>{err.title}</AlertTitle>
              <AlertDescription>{err.message}</AlertDescription>
            </Alert>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                {isLocalAuth
                  ? "Sign in"
                  : isMockAuth || !isOidcEnabled
                    ? "Continue (mock mode)"
                    : `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
              </CardTitle>
              <CardDescription>
                {isLocalAuth
                  ? "Use the email and password for your account. Accounts are created by your administrator."
                  : isMockAuth || !isOidcEnabled
                    ? "Auth0 isn't configured — using a local session cookie. Pick a role to preview permissions."
                    : `Use your work account. Enterprise SSO is configured by your ${BUSINESS_UNIT_LABEL.toLowerCase()} admin.`}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {/* Local email+password (Phase 3) takes precedence when AUTH_MODE=local.
                  Otherwise: Auth0 button when OIDC is enabled and not mock; else mock. */}
              {isLocalAuth ? (
                <EmailPasswordForm redirectTo={redirectTo} />
              ) : !isMockAuth && isOidcEnabled ? (
                <Auth0SignInButton redirectTo={redirectTo} />
              ) : (
                <MockSignInPanel redirectTo={redirectTo} />
              )}
            </CardContent>
          </Card>

          {!isMockAuth && isOidcEnabled && (
            <p className="text-muted-foreground text-center text-xs">
              Trouble signing in?{" "}
              <Link href="#" className="text-foreground underline underline-offset-2">
                Contact your admin
              </Link>{" "}
              or{" "}
              <a
                href="mailto:support@example.test"
                className="text-foreground underline underline-offset-2"
              >
                email support
              </a>
              .
            </p>
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
        </section>
      </div>
    </main>
  );
}

function FeatureRow({
  icon: Icon,
  title,
  blurb,
}: {
  icon: LucideIcon;
  title: string;
  blurb: string;
}) {
  return (
    <li className="flex items-start gap-3">
      <div className="bg-muted text-muted-foreground mt-0.5 grid size-8 shrink-0 place-items-center rounded-md border">
        <Icon className="size-4" aria-hidden />
      </div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-sm font-medium">{title}</span>
        <span className="text-muted-foreground text-sm">{blurb}</span>
      </div>
    </li>
  );
}
