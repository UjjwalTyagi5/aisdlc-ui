import type { Metadata } from "next";
import Link from "next/link";
import { ShieldAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  SIGN_IN_SURFACE,
  SignInBrandLockup,
  SignInMethod,
  SignInTerms,
  signInDescription,
  signInTitle,
} from "@/components/landing/sign-in-content";
import { isMockAuth, isOidcEnabled } from "@/lib/auth/mode";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

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

/**
 * `/login` — the same sign-in as the landing popup, on its own page.
 *
 * ONE CENTERED CARD, not a two-column layout. This route used to put a marketing panel
 * beside the form while the popup had already become the form alone, on a newer
 * surface. It is not a cosmetic difference: an expired session and every emailed
 * password link land here, so the older-looking one was what a returning user saw
 * first. Everything inside the card now comes from `sign-in-content.tsx`, shared with
 * the popup.
 *
 * What stays here, because the popup has no use for it: the error banner (this is
 * where `?error=` lands) and the `?from=` redirect.
 */
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
    <main className="bg-background relative grid min-h-dvh place-items-center px-6 py-12">
      {/* The same atmosphere the popup sits on, so the two read as one product. Both
          layers are decorative and pointer-events-none, so neither can intercept a
          click on the form. */}
      <div aria-hidden className="bg-mesh pointer-events-none absolute inset-0 -z-10 opacity-70" />
      <div
        aria-hidden
        className="from-primary/5 via-background to-background pointer-events-none absolute inset-x-0 top-0 -z-10 h-80 bg-gradient-to-b"
      />

      <section className="w-full max-w-lg space-y-4" aria-label="Sign in">
        {err && (
          <Alert variant="destructive">
            <ShieldAlert />
            <AlertTitle>{err.title}</AlertTitle>
            <AlertDescription>{err.message}</AlertDescription>
          </Alert>
        )}

        {/* `relative` is THIS host's, not the shared surface's: it is what contains the
            glow below. The dialog must not inherit it — see SIGN_IN_SURFACE. */}
        <div data-testid="sign-in-card" className={`${SIGN_IN_SURFACE} relative`}>
          <div
            aria-hidden
            className="bg-mesh pointer-events-none absolute inset-0 -z-10 rounded-2xl opacity-80"
          />
          <SignInBrandLockup />
          <div className="space-y-1.5 text-center">
            <h1 className="font-display text-2xl tracking-tight">{signInTitle()}</h1>
            <p className="text-muted-foreground text-sm">{signInDescription()}</p>
          </div>
          <SignInMethod redirectTo={redirectTo} />
          <SignInTerms />
        </div>

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
      </section>
    </main>
  );
}
