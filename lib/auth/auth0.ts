import { Auth0Client } from "@auth0/nextjs-auth0/server";

/**
 * Lazy Auth0 client — instantiated on first access so that mock-mode
 * developers don't need AUTH0_* env vars just to run the app.
 *
 * Routes mounted by `auth0.middleware()`:
 *   /auth/login, /auth/logout, /auth/callback, /auth/access-token, /auth/profile
 */
let _client: Auth0Client | null = null;

function required(name: string): string {
  const v = process.env[name];
  if (!v) {
    throw new Error(
      `[auth0] Missing env var ${name}. Set NEXT_PUBLIC_AUTH_MODE=mock, or fill in .env from .env.example.`,
    );
  }
  return v;
}

export function getAuth0(): Auth0Client {
  if (_client) return _client;
  _client = new Auth0Client({
    domain: required("AUTH0_DOMAIN"),
    clientId: required("AUTH0_CLIENT_ID"),
    clientSecret: required("AUTH0_CLIENT_SECRET"),
    secret: required("AUTH0_SECRET"),
    appBaseUrl: required("APP_BASE_URL"),
    authorizationParameters: {
      scope: process.env.AUTH0_SCOPE ?? "openid profile email offline_access",
      audience: process.env.AUTH0_AUDIENCE,
    },
  });
  return _client;
}
