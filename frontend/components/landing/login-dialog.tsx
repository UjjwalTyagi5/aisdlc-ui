"use client";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
  SIGN_IN_SURFACE,
  SignInBrandLockup,
  SignInMethod,
  SignInTerms,
  signInDescription,
  signInTitle,
} from "@/components/landing/sign-in-content";

/**
 * Sign-in as a small, centered popup on the marketing landing — just the form, no
 * marketing copy (that's what the page behind it is for).
 *
 * Everything inside comes from `sign-in-content.tsx`, shared with the `/login` route so
 * the two cannot disagree about the wording, the auth mode on offer, or the surface.
 * They had drifted twice before that module existed.
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
      <DialogContent className={`${SIGN_IN_SURFACE} sm:max-w-lg sm:rounded-2xl`}>
        {/* Ambient brand-glow. Contained to the dialog's own box: a fixed-position
            ancestor is a containing block, so this never bleeds past the corners. */}
        <div aria-hidden className="bg-mesh pointer-events-none absolute inset-0 -z-10 rounded-2xl opacity-80" />
        <SignInBrandLockup />
        <div className="space-y-1.5 text-center">
          {/* Radix's own title and description, so the dialog stays labelled for a
              screen reader. The words are the shared ones. */}
          <DialogTitle className="font-display text-2xl tracking-tight">
            {signInTitle()}
          </DialogTitle>
          <DialogDescription>{signInDescription()}</DialogDescription>
        </div>
        <SignInMethod redirectTo={redirectTo} />
        <SignInTerms />
      </DialogContent>
    </Dialog>
  );
}
