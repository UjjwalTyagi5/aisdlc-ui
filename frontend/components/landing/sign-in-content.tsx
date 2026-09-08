import { PwcMark } from "@/components/brand/pwc-mark";
import { Auth0SignInButton } from "@/app/(auth)/login/auth0-signin-button";
import { EmailPasswordForm } from "@/app/(auth)/login/email-password-form";
import { MockSignInPanel } from "@/app/(auth)/login/mock-signin-panel";
import { isLocalAuth, isMockAuth, isOidcEnabled } from "@/lib/auth/mode";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * What sign-in SAYS, owned once and rendered by both hosts: the landing popup
 * (`login-dialog.tsx`) and the `/login` route.
 *
 * THE TWO HAD DRIFTED TWICE. `sign-in-brand-panel.tsx` was written after the first
 * time and shared the marketing half — the half that mattered less. The heading, the
 * blurb, the auth-mode branch and the terms stayed copied into both files, so the
 * popup became a single centered card while the route kept a two-column layout beside
 * a marketing panel, with older surface treatment. That is the version an expired
 * session and every emailed password link land on, so the drifted one was the first
 * thing a returning user saw.
 *
 * Each host keeps only its own frame — a Dialog, or a page and its card — and takes
 * everything inside it from here. The heading and blurb are exported as values rather
 * than as a component because the popup must render them through Radix's
 * `DialogTitle` / `DialogDescription` to stay labelled for a screen reader, while the
 * page uses plain elements.
 *
 * NO `"use client"` HERE, deliberately. `/login` is a server component and CALLS
 * `signInTitle()` / `signInDescription()` — across a client boundary that is not a
 * call, it is a serialized reference, and Next throws "Attempted to call signInTitle()
 * from the server". Every piece below that genuinely needs the client carries its own
 * directive (`EmailPasswordForm`, `MockSignInPanel`), which is what lets this module
 * stay usable from both sides. Caught by serving the route; jsdom does not enforce the
 * boundary, so the unit tests passed with it broken.
 */

export const signInTitle = (): string =>
  isLocalAuth
    ? `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`
    : isMockAuth || !isOidcEnabled
      ? "Continue (mock mode)"
      : `Sign in to your ${BUSINESS_UNIT_LABEL.toLowerCase()}`;

export const signInDescription = (): string =>
  isLocalAuth
    ? "Use the email and password set up by your administrator."
    : isMockAuth || !isOidcEnabled
      ? "Auth0 isn't configured — using a local session cookie. Pick a role to preview permissions."
      : `Use your work account. Enterprise SSO is configured by your ${BUSINESS_UNIT_LABEL.toLowerCase()} admin.`;

/** The mark and wordmark, centered above the heading. */
export function SignInBrandLockup() {
  return (
    <div className="flex items-center justify-center gap-3">
      <PwcMark size={40} />
      <span className="font-display text-sm font-bold tracking-tight">SDLC Platform</span>
    </div>
  );
}

/**
 * Whichever credential UI this deployment actually uses.
 *
 * Local email+password takes precedence when AUTH_MODE=local; otherwise the Auth0
 * button when OIDC is enabled and not mock; else the mock role picker. Branching in
 * one place is the point — two copies is how a host ends up offering a sign-in method
 * the backend is not running.
 */
export function SignInMethod({ redirectTo }: { redirectTo: string }) {
  if (isLocalAuth) return <EmailPasswordForm redirectTo={redirectTo} />;
  if (!isMockAuth && isOidcEnabled) return <Auth0SignInButton redirectTo={redirectTo} />;
  return <MockSignInPanel redirectTo={redirectTo} />;
}

export function SignInTerms() {
  return (
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
  );
}

/**
 * The card's own surface, shared so the popup and the route cannot look like different
 * products. Translucent panel, soft border, heavy blur, rounded — with the mesh glow
 * behind it, contained to the card's own box so it never bleeds past the corners.
 */
export const SIGN_IN_SURFACE =
  "border-line-soft/50 bg-panel-elevated/60 relative flex flex-col gap-5 rounded-2xl p-8 shadow-2xl backdrop-blur-2xl sm:p-10";
