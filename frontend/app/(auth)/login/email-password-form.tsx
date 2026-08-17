"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, Info, Loader2, Lock, Mail } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Mode = "signin" | "signup";

/**
 * Combined email+password sign-in and sign-up (local auth).
 *
 * Both live on the login page as two tabs over one card rather than two routes:
 * there is exactly one organization and one way in, so a separate /register page
 * only added a hop.
 *
 * Signing up creates an account with NO permissions — it joins the organization
 * but carries no role bindings until an admin grants one. The copy says so before
 * submit, and the server sends the new account to /my-access, because a brand-new
 * user who lands on a dashboard that refuses them reads it as a broken app.
 */
export function EmailPasswordForm({ redirectTo }: { redirectTo: string }) {
  const router = useRouter();
  const [mode, setMode] = React.useState<Mode>("signin");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const isSignup = mode === "signup";
  const passwordTooShort = isSignup && password.length > 0 && password.length < 8;

  function switchMode(next: string) {
    setMode(next as Mode);
    // Carry the email across — someone who mistyped their way into the wrong tab
    // should not have to retype it. The password is cleared deliberately: the
    // autocomplete semantics differ (current-password vs new-password) and a
    // reused value in the wrong field is a footgun.
    setPassword("");
    setError(null);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(isSignup ? "/api/auth/register" : "/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        redirectTo?: string;
        error?: string;
      };
      if (!res.ok) {
        setError(
          data.error ??
            (isSignup
              ? "Sign-up failed. Please try again."
              : "Sign-in failed. Please try again."),
        );
        setSubmitting(false);
        return;
      }
      router.push(data.redirectTo ?? redirectTo);
      router.refresh();
    } catch {
      setError("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  }

  return (
    <Tabs value={mode} onValueChange={switchMode} className="flex flex-col gap-4">
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger value="signin" disabled={submitting}>
          Sign in
        </TabsTrigger>
        <TabsTrigger value="signup" disabled={submitting}>
          Create account
        </TabsTrigger>
      </TabsList>

      {/* One form serves both tabs. TabsContent carries only the mode-specific
          copy so switching never remounts the inputs and loses what was typed. */}
      <TabsContent value="signin" className="mt-0" />
      <TabsContent value="signup" className="mt-0">
        <Alert>
          <Info />
          <AlertDescription>
            New accounts start with no access. You&rsquo;ll be able to sign in
            straight away, but an administrator has to assign your role before
            anything opens up.
          </AlertDescription>
        </Alert>
      </TabsContent>

      <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        {error && (
          <Alert variant="destructive" className="animate-in fade-in-50 slide-in-from-top-1">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Work email</Label>
          <div className="relative">
            <Mail
              className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2"
              aria-hidden
            />
            <Input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={submitting}
              className="pl-9"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <div className="relative">
            <Lock
              className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2"
              aria-hidden
            />
            <Input
              id="password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete={isSignup ? "new-password" : "current-password"}
              placeholder={isSignup ? "At least 8 characters" : "••••••••••••"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={isSignup ? 8 : undefined}
              disabled={submitting}
              aria-invalid={passwordTooShort || undefined}
              aria-describedby={passwordTooShort ? "password-hint" : undefined}
              className="px-9"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              tabIndex={-1}
              aria-label={showPassword ? "Hide password" : "Show password"}
              className="text-muted-foreground hover:text-foreground absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 transition-colors"
            >
              {showPassword ? (
                <EyeOff className="size-4" aria-hidden />
              ) : (
                <Eye className="size-4" aria-hidden />
              )}
            </button>
          </div>
          {passwordTooShort && (
            <p id="password-hint" className="text-destructive text-xs">
              Must be at least 8 characters.
            </p>
          )}
        </div>

        <Button
          type="submit"
          disabled={submitting || passwordTooShort}
          className="mt-1 w-full"
        >
          {submitting ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden />
              {isSignup ? "Creating your account…" : "Signing in…"}
            </>
          ) : isSignup ? (
            "Create account"
          ) : (
            "Sign in"
          )}
        </Button>
      </form>
    </Tabs>
  );
}
