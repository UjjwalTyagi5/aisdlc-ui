import * as React from "react";
import { AlertTriangle, Lock, RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { ApiError } from "@/lib/schemas";

export interface ApiErrorStateProps {
  /** Structured API error — renders user-safe message + code; never raw backend stack. */
  error?: ApiError | null;
  /** Fallback title when no ApiError provided. */
  title?: string;
  /** Fallback description when no ApiError provided. */
  description?: React.ReactNode;
  /** Retry callback — wired to the PwC-orange primary Button. */
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
  /** Rendered below the message, ONLY in the forbidden branch — "request access"
   *  makes no sense for a genuine error, only for a permission limit. */
  action?: React.ReactNode;
}

/**
 * Elevated gap-state for API errors. Binds the ApiError {code, message, requestId?}
 * shape (ELEVATION-PLAN §9). Never renders raw backend stack or detail field.
 *
 * A `forbidden` code (normalized from a backend 403 by the BFF proxy) gets a
 * distinct, calmer treatment: it tells the user this is a permission limit at
 * their role — not a system failure — and omits the retry CTA (retrying a 403
 * can never succeed).
 */
export function ApiErrorState({
  error,
  title,
  description,
  onRetry,
  retryLabel = "Try again",
  className,
  action,
}: ApiErrorStateProps) {
  const forbidden = error?.code === "forbidden";

  const displayTitle = forbidden
    ? "You don't have access"
    : (title ?? "Something went wrong");
  const displayMessage = forbidden
    ? (error?.message ??
      "You don't have permission to view this at your current role. Ask an admin to grant you the right role.")
    : (error?.message ??
      (typeof description === "string"
        ? description
        : "We couldn't complete that request. Please retry — if the issue persists, contact support."));

  const Icon = forbidden ? Lock : AlertTriangle;

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={cn(
        "flex flex-col items-center gap-5 px-6 py-12 text-center",
        className,
      )}
    >
      {/* Icon halo — calmer amber for permission limits, destructive for real errors */}
      <div
        className={cn(
          "grid size-14 place-items-center rounded-full border",
          forbidden
            ? "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400"
            : "border-destructive/20 bg-destructive/8 text-destructive shadow-[0_0_18px_-4px_oklch(0.577_0.245_27.325_/_0.3)]",
        )}
      >
        <Icon className="size-6" aria-hidden />
      </div>

      {/* Editorial heading */}
      <div className="space-y-2">
        <h3 className="font-display text-xl font-bold tracking-[-0.02em]">{displayTitle}</h3>
        <p className="mx-auto max-w-md text-sm text-muted-foreground">{displayMessage}</p>
      </div>

      {/* Structured error metadata — only for real errors, never for a clean permission limit */}
      {!forbidden && error && (error.code || error.requestId) && (
        <Alert variant="destructive" className="mx-auto max-w-sm text-left">
          {error.code && (
            <AlertTitle className="font-mono text-xs uppercase tracking-widest">
              {error.code}
            </AlertTitle>
          )}
          {error.requestId && (
            <AlertDescription className="font-mono text-[11px] text-muted-foreground">
              Request ID: {error.requestId}
            </AlertDescription>
          )}
        </Alert>
      )}

      {/* PwC-orange primary retry CTA — suppressed for forbidden (retry can't help) */}
      {!forbidden && onRetry && (
        <Button
          onClick={onRetry}
          className="bg-gradient-to-br from-brand-gradient-from to-brand-gradient-to font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.55)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.7)] focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
        >
          <RefreshCw className="size-4" aria-hidden />
          {retryLabel}
        </Button>
      )}

      {forbidden && action}
    </div>
  );
}
