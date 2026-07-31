"use client";

import * as React from "react";

/**
 * Last-resort error boundary. Fires when a render error escapes the `(app)`
 * boundary or happens in the root layout itself. Must include its own <html>
 * + <body> because Next may have failed to render the root layout.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    console.error("[global-error]", error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          fontFamily:
            'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
          margin: 0,
          minHeight: "100dvh",
          display: "grid",
          placeItems: "center",
          padding: "2rem",
          background: "#fafafa",
          color: "#0a0a0a",
        }}
      >
        <div
          style={{
            maxWidth: 480,
            width: "100%",
            background: "white",
            border: "1px solid rgba(0,0,0,0.08)",
            borderRadius: 12,
            padding: 24,
            boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
          }}
          role="alert"
        >
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            Something went wrong
          </h1>
          <p style={{ color: "#525252", fontSize: 14, marginTop: 8 }}>
            The app hit an unexpected error. This incident has been logged.
          </p>
          {error.digest && (
            <p
              style={{
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace",
                fontSize: 11,
                color: "#737373",
                marginTop: 12,
              }}
            >
              digest: {error.digest}
            </p>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button
              type="button"
              onClick={reset}
              style={{
                background: "#0a0a0a",
                color: "white",
                padding: "8px 14px",
                borderRadius: 8,
                border: 0,
                fontSize: 14,
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              Try again
            </button>
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages --
                 global-error renders outside the Next router; next/link is not available here. */}
            <a
              href="/projects"
              style={{
                padding: "8px 14px",
                borderRadius: 8,
                border: "1px solid rgba(0,0,0,0.12)",
                color: "#0a0a0a",
                textDecoration: "none",
                fontSize: 14,
                fontWeight: 500,
              }}
            >
              Back to Projects
            </a>
          </div>
        </div>
      </body>
    </html>
  );
}
