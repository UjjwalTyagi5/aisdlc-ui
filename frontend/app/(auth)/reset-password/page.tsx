import type { Metadata } from "next";
import Link from "next/link";

import { PwcMark } from "@/components/brand/pwc-mark";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { ResetPasswordForm } from "./reset-password-form";

export const metadata: Metadata = { title: "Set your password" };

/**
 * The page every set-password link lands on — onboarding invite AND forgotten password.
 *
 * One page for both because they are one mechanism: a single-use token that authorises
 * choosing a password. The only difference is the wording, and the token's `purpose`
 * is not exposed here — a visitor who was just onboarded does not need to be told which
 * kind of link they hold, and the copy reads correctly for either.
 *
 * Reachable without a session by design (middleware PUBLIC_PREFIXES): an onboarded
 * account has no password yet, so its owner cannot possibly be signed in.
 */
export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;

  return (
    <main className="bg-background grid min-h-dvh place-items-center px-6 py-12">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <PwcMark size={44} />
          <h1 className="text-xl font-semibold tracking-tight">SDLC Platform</h1>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Choose your password</CardTitle>
            <CardDescription>
              Pick something you don&rsquo;t use anywhere else. At least 8 characters.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ResetPasswordForm token={token ?? ""} />
          </CardContent>
        </Card>

        <p className="text-muted-foreground text-center text-sm">
          <Link href="/login" className="hover:text-foreground underline underline-offset-2">
            Back to sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
