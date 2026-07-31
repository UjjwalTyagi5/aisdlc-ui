"use client";

import * as React from "react";

import { ErrorState } from "@/components/ui/error-state";

/**
 * Boundary for anything rendered inside the `(app)` layout. Keeps the shell
 * (sidebar + top bar) mounted so the user can navigate away or retry.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    console.error("[app-error]", error);
  }, [error]);

  return (
    <div className="mx-auto max-w-2xl p-4 md:p-8">
      <ErrorState
        title="This page ran into a problem"
        description="Retry — or head back to Projects. If it keeps failing, include the digest below when you contact support."
        detail={
          error.digest
            ? `digest: ${error.digest}\n${error.message}`
            : error.message
        }
        onRetry={reset}
      />
    </div>
  );
}
