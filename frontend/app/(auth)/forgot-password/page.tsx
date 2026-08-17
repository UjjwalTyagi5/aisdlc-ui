import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { PwcMark } from "@/components/brand/pwc-mark";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { ForgotPasswordForm } from "./forgot-password-form";

export const metadata: Metadata = { title: "Forgot password" };

/**
 * Request a password-reset link.
 *
 * Reachable without a session — see the PUBLIC_PREFIXES note in middleware.ts. Someone
 * who has lost their onboarding invite has no password at all, so this is their only way
 * in, not merely a recovery path.
 */
export default function ForgotPasswordPage() {
  return (
    <main className="bg-background grid min-h-dvh place-items-center px-6 py-12">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <PwcMark size={44} />
          <h1 className="text-xl font-semibold tracking-tight">SDLC Platform</h1>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Reset your password</CardTitle>
            <CardDescription>
              Enter your work email and we&rsquo;ll send you a link to choose a new
              password.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ForgotPasswordForm />
          </CardContent>
        </Card>

        <p className="text-center text-sm">
          <Link
            href="/login"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 underline underline-offset-2"
          >
            <ArrowLeft className="size-3.5" aria-hidden />
            Back to sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
