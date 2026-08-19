import type { Metadata } from "next";
import Link from "next/link";
import { ShieldAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PwcMark } from "@/components/brand/pwc-mark";
import { SignInBrandPanel } from "@/components/landing/sign-in-brand-panel";
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
      {/* THE SAME SURFACE AS THE LANDING POPUP. This route had drifted to a flat
          panel with plain type and older copy, which mattered more than it sounds:
          /login is where an expired session and every emailed password link land, so
          the drifted one was the first thing a returning user saw. */}
      <div
        aria-hidden
        className="bg-mesh pointer-events-none absolute inset-0 -z-10 opacity-70"
      />
      <div
        aria-hidden
        className="from-primary/5 via-background to-background pointer-events-none absolute inset-x-0 top-0 -z-10 h-80 bg-gradient-to-b"
      />

      <div className="mx-auto grid min-h-dvh w-full max-w-6xl grid-cols-1 items-center gap-12 px-6 py-12 lg:grid-cols-[1fr_420px] lg:py-0">
        {/* Marketing column — shared with the landing popup so the two cannot
            disagree about what the product says about itself. */}
        <SignInBrandPanel className="hidden py-12 lg:flex" />

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

          <Card className="border-line-soft/60 bg-panel-elevated/80 shadow-2xl backdrop-blur-2xl">
            <CardHeader>
              <CardTitle className="font-display text-2xl tracking-tight">
                {isLocalAuth
                  ? `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`
                  : isMockAuth || !isOidcEnabled
                    ? "Continue (mock mode)"
                    : `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
              </CardTitle>
              <CardDescription>
                {isLocalAuth
                  ? "Use the email and password set up by your administrator."
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
