"use client";

import * as React from "react";
import { CheckCircle2, Loader2, Mail } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Request a reset link.
 *
 * THE CONFIRMATION IS DELIBERATELY VAGUE. "If an account exists for that address, a link
 * is on its way" — never "we've sent you an email", because the difference between those
 * two sentences is whether this page tells a stranger which of your colleagues have
 * accounts. The backend answers identically either way and the UI must not undo that.
 *
 * Success is terminal: the form is replaced rather than reset. Leaving an enabled submit
 * button invites the impatient to generate three links, of which only the last works.
 */
export function ForgotPasswordForm() {
  const [email, setEmail] = React.useState("");
  const [sent, setSent] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        setError(data.error ?? "Something went wrong. Please try again.");
        setSubmitting(false);
        return;
      }
      setSent(true);
    } catch {
      setError("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <Alert>
        <CheckCircle2 />
        <AlertDescription>
          If an account exists for that address, a link to choose a new password is on
          its way. It can be used once and expires in a couple of hours.
          <br />
          <span className="text-muted-foreground mt-2 inline-block">
            Nothing arrived? Check spam, then ask your administrator — they can send a
            fresh invitation.
          </span>
        </AlertDescription>
      </Alert>
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

      <Button type="submit" disabled={submitting} className="w-full">
        {submitting ? (
          <>
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Sending…
          </>
        ) : (
          "Send reset link"
        )}
      </Button>
    </form>
  );
}
