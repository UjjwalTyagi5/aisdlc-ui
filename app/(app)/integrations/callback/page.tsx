"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { completeOAuthCallback } from "@/lib/api/connectors";
import { qk } from "@/lib/api/query-keys";

/**
 * OAuthCallbackHandler — Wave C (REQ-M7-22), Connect Flow steps 4-6.
 *
 * The OAuth provider redirects the user's browser to
 * /integrations/callback?code=...&state=...&kind=... after consent.
 * This page:
 *   1. Reads code, state, kind from the URL search params.
 *   2. On mount, calls the BFF callback route (GET /api/connectors/{kind}/oauth/callback).
 *   3. Shows a full-page loading state while in flight.
 *   4. On success: invalidates the connectors list cache + redirects to
 *      /integrations?connected={kind} so the integrations page fires the
 *      success toast (Connect Flow step 5).
 *   5. On error: renders ApiErrorState (Connect Flow step 6).
 *
 * Security: the raw OAuth code+state are forwarded to FastAPI which validates
 * the HMAC CSRF state server-side (T-7.4-23). Tokens are NEVER sent to the
 * browser (T-7.4-24) — only the resulting Connector record is returned.
 */
export default function OAuthCallbackHandler() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();

  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const code = searchParams.get("code") ?? "";
    const state = searchParams.get("state") ?? "";
    const kind = searchParams.get("kind") ?? "";

    if (!code || !state || !kind) {
      setError(
        "OAuth was cancelled, the state parameter was invalid, or the provider returned an error. Go back to Integrations and try again.",
      );
      return;
    }

    completeOAuthCallback(kind, code, state)
      .then(() => {
        queryClient.invalidateQueries({ queryKey: qk.connectors.list() });
        router.push(`/integrations?connected=${encodeURIComponent(kind)}`);
      })
      .catch((err: unknown) => {
        const message =
          err instanceof Error
            ? err.message
            : "OAuth was cancelled, the state parameter was invalid, or the provider returned an error. Go back to Integrations and try again.";
        setError(message);
      });
  // Run once on mount — searchParams and queryClient are stable references.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div className="mx-auto max-w-5xl p-4 md:p-8">
        <ApiErrorState
          title="Connection failed"
          description={error}
          onRetry={() => router.push("/integrations")}
          retryLabel="Back to Integrations"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-8">
      <LoadingState variant="card" label="Connecting" />
    </div>
  );
}
