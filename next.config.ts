import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

/**
 * CSP policy.
 *
 * Notes on the relaxed bits:
 *  - `script-src 'unsafe-inline' 'unsafe-eval'` in dev only — Next HMR needs both.
 *    Production drops `'unsafe-eval'`.
 *  - `style-src 'unsafe-inline'` is required by `next/font` and runtime CSS-in-JS
 *    (Sonner, Radix inline styles). Nonces work for app CSS but would break
 *    those packages — revisit when they ship CSP-compatible releases.
 *  - `connect-src` includes Auth0 + our own origin. Extend for Sentry / Datadog
 *    when they land in Sprint 7.
 *  - `worker-src blob:` is required by Monaco.
 */
const CSP_PROD = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "worker-src 'self' blob:",
  "child-src 'self' blob:",
  "script-src 'self' 'unsafe-inline'",
  "connect-src 'self' https://*.auth0.com",
  "media-src 'self'",
  "manifest-src 'self'",
  "upgrade-insecure-requests",
].join("; ");

const CSP_DEV = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "worker-src 'self' blob:",
  "child-src 'self' blob:",
  // Dev HMR needs inline + eval
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  // Next dev server websockets for HMR
  "connect-src 'self' ws: wss: https://*.auth0.com",
  "media-src 'self'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: isDev ? CSP_DEV : CSP_PROD },
  { key: "X-DNS-Prefetch-Control", value: "on" },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  // NOTE: Cross-Origin-Resource-Policy intentionally omitted so MSW + service-
  // worker requests work cleanly in dev. Re-enable when we have a full audit
  // of embed boundaries.
];

const baseConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Emits .next/standalone with a self-contained server.js + pruned node_modules,
  // so the container image ships ~150MB instead of the full dev tree.
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react", "@tanstack/react-query", "date-fns"],
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

// Optional bundle analyzer — opt in via `ANALYZE=true pnpm build`.
function maybeWithAnalyzer(cfg: NextConfig): NextConfig {
  if (process.env.ANALYZE !== "true") return cfg;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const withAnalyzer = require("@next/bundle-analyzer")({ enabled: true });
    return withAnalyzer(cfg) as NextConfig;
  } catch {
    console.warn(
      "[next.config] ANALYZE=true but @next/bundle-analyzer is not installed. " +
        "Run `pnpm add -D @next/bundle-analyzer` first.",
    );
    return cfg;
  }
}

export default maybeWithAnalyzer(baseConfig);
