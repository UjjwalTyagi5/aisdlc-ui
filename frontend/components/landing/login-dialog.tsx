"use client";

import { PwcMark } from "@/components/brand/pwc-mark";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Auth0SignInButton } from "@/app/(auth)/login/auth0-signin-button";
import { EmailPasswordForm } from "@/app/(auth)/login/email-password-form";
import { MockSignInPanel } from "@/app/(auth)/login/mock-signin-panel";
import { isLocalAuth, isMockAuth, isOidcEnabled } from "@/lib/auth/mode";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";


/**
 * Sign-in as a small, centered popup on the marketing landing — just the
 * form, no marketing copy (that's what the page behind it is for). Reuses
 * the same EmailPasswordForm as /login, so behaviour (tier redirect, errors)
 * matches.
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
      <DialogContent className="border-line-soft/60 bg-panel-elevated/80 flex flex-col gap-5 p-8 shadow-2xl backdrop-blur-2xl sm:max-w-md sm:p-10">
        <div className="flex items-center justify-center gap-3">
          <PwcMark size={40} />
          <span className="font-display text-sm font-bold tracking-tight">SDLC Platform</span>
        </div>
        <div className="space-y-1.5 text-center">
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
      </DialogContent>
    </Dialog>
  );
}
