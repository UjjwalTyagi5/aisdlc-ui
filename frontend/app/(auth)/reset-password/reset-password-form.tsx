"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, Eye, EyeOff, Loader2, Lock, ShieldAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type TokenState = "checking" | "ok" | "expired" | "used" | "unknown" | "unchecked";

const BAD_TOKEN: Record<string, { title: string; message: string }> = {
  expired: {
    title: "This link has expired",
    message:
      "Links are short-lived on purpose. Request a new one and it will arrive in a moment.",
  },
  used: {
    title: "This link has already been used",
    message:
      "Each link works once. If you already set a password, sign in with it — otherwise request a new link.",
  },
  unknown: {
    title: "This link isn't valid",
    message:
      "It may have been copied incompletely from the email. Try clicking it again, or request a new one.",
  },
};

/**
 * Set a password using a single-use link.
 *
 * THE TOKEN IS CHECKED BEFORE THE FORM RENDERS, via a read-only endpoint that does not
 * spend it. Without that, somebody with an expired link types a password twice and only
 * then learns it was never going to work — and the natural conclusion is that the
 * password was rejected, not the link.
 *
 * `unchecked` is distinct from `unknown`: it means the validity check itself failed to
 * reach the server, in which case the form is shown and the submit becomes the arbiter.
 * Telling someone their link is broken because our own request was is worse than trying.
 */
export function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
  const [state, setState] = React.useState<TokenState>(token ? "checking" : "unknown");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [show, setShow] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [done, setDone] = React.useState(false);

  React.useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/auth/reset-password?token=${encodeURIComponent(token)}`,
        );
        const data = (await res.json().catch(() => ({}))) as { status?: string };
        if (!cancelled) setState((data.status as TokenState) ?? "unknown");
      } catch {
        if (!cancelled) setState("unchecked");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const tooShort = password.length > 0 && password.length < 8;
  const mismatch = confirm.length > 0 && confirm !== password;
  const canSubmit = password.length >= 8 && confirm === password && !submitting;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        setError(data.error ?? "Couldn't set your password. Try again.");
        setSubmitting(false);
        return;
      }
      setDone(true);
    } catch {
      setError("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="flex flex-col gap-4">
        <Alert>
          <CheckCircle2 />
          <AlertTitle>Password set</AlertTitle>
          <AlertDescription>
            You can sign in with it now.
          </AlertDescription>
        </Alert>
        {/* Not an automatic redirect: somebody who has just chosen a password benefits
            from a beat to register that it worked before the screen changes under them. */}
        <Button className="w-full" onClick={() => router.push("/login")}>
          Go to sign in
        </Button>
      </div>
    );
  }

  if (state === "checking") {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        Checking your link…
      </p>
    );
  }

  const bad = BAD_TOKEN[state];
  if (bad) {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="destructive">
          <ShieldAlert />
          <AlertTitle>{bad.title}</AlertTitle>
          <AlertDescription>{bad.message}</AlertDescription>
        </Alert>
        <Button asChild variant="outline" className="w-full">
          <Link href="/forgot-password">Request a new link</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="password">New password</Label>
        <div className="relative">
          <Lock
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            id="password"
            name="password"
            type={show ? "text" : "password"}
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            disabled={submitting}
            aria-invalid={tooShort || undefined}
            aria-describedby={tooShort ? "pw-hint" : undefined}
            className="px-9"
          />
          <button
            type="button"
            onClick={() => setShow((v) => !v)}
            tabIndex={-1}
            aria-label={show ? "Hide password" : "Show password"}
            className="text-muted-foreground hover:text-foreground absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 transition-colors"
          >
            {show ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
          </button>
        </div>
        {tooShort && (
          <p id="pw-hint" className="text-destructive text-xs">
            Must be at least 8 characters.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="confirm">Confirm password</Label>
        <div className="relative">
          <Lock
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            id="confirm"
            name="confirm"
            type={show ? "text" : "password"}
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
            disabled={submitting}
            aria-invalid={mismatch || undefined}
            aria-describedby={mismatch ? "confirm-hint" : undefined}
            className="pl-9"
          />
        </div>
        {mismatch && (
          <p id="confirm-hint" className="text-destructive text-xs">
            Both passwords must match.
          </p>
        )}
      </div>

      {state === "unchecked" && (
        <p className="text-muted-foreground text-xs">
          We couldn&rsquo;t verify your link just now — go ahead and set your password,
          and we&rsquo;ll tell you if it didn&rsquo;t work.
        </p>
      )}

      <Button type="submit" disabled={!canSubmit} className="w-full">
        {submitting ? (
          <>
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Setting your password…
          </>
        ) : (
          "Set password"
        )}
      </Button>
    </form>
  );
}
