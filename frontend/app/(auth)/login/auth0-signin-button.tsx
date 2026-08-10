import Link from "next/link";
import { LogIn } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Kicks off the Auth0 Universal Login flow. The SDK middleware handles
 * `/auth/login` → IdP redirect → `/auth/callback`.
 */
export function Auth0SignInButton({ redirectTo }: { redirectTo: string }) {
  const href = `/auth/login?returnTo=${encodeURIComponent(redirectTo)}`;
  return (
    <Button asChild className="w-full">
      <Link href={href}>
        <LogIn className="size-4" aria-hidden />
        Continue with SSO
      </Link>
    </Button>
  );
}
