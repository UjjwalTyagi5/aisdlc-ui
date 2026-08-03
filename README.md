# `@sdlc/web` — SDLC Platform web app

**MVP-0 web app complete — Chunks 1 – 18 shipped.** Foundation · primitives · shell · auth · data contract · composites · 6 phase pages · admin surfaces · onboarding wizard · real-time streaming · hardening · **PwC brand (orange primary + logo)**.

## Stack (locked)

- **Next.js 15** (App Router, TS strict, Turbopack dev)
- **React 19**
- **Tailwind v4** (CSS-first `@theme`, OKLCH tokens)
- **shadcn/ui** (new-york, neutral base, CSS variables)
- **TanStack Query v5** (+ devtools)
- **next-themes** (class-based dark mode with system preference)
- **Sonner** (toast)
- **Lucide** (icons)
- **ESLint 9** (flat config) + **Prettier** + **lint-staged** + **Husky**

## Run

```bash
# from repo root
cd apps/web
pnpm install
pnpm dev          # http://localhost:3000 (Turbopack)
pnpm storybook    # http://localhost:6006
```

Sanity checks:

```bash
pnpm typecheck          # tsc --noEmit, strict
pnpm lint               # max-warnings 0
pnpm build              # production build
pnpm build-storybook    # static build of Storybook
pnpm format             # prettier write
```

## What's in this chunk

| Path | Purpose |
|---|---|
| `app/layout.tsx` | Root layout, font variables (Inter + JetBrains Mono), providers |
| `app/page.tsx` | Themed smoke-test page (swatches, tones, phase pipeline preview, button matrix) |
| `app/globals.css` | Tailwind v4 import, design tokens (light + dark), `@theme inline` wiring |
| `components/providers.tsx` | `ThemeProvider` + `QueryClientProvider` + `Toaster` (+ devtools in dev) |
| `components/theme-toggle.tsx` | Hydration-safe light/dark toggle |
| `components/ui/button.tsx` | shadcn Button (7 variants × 4 sizes, `asChild` via Radix Slot) |
| `components/ui/card.tsx` | shadcn Card primitives |
| `lib/utils.ts` | `cn()` helper (clsx + tailwind-merge v3) |
| `components.json` | shadcn CLI config — run `pnpm dlx shadcn@latest add <name>` from Chunk 2 onward |
| `next.config.ts` | Security headers (HSTS, X-Frame-Options, Permissions-Policy, Referrer-Policy) |
| `eslint.config.mjs` | Flat config: `next/core-web-vitals` + `next/typescript` + strict rules |
| `tsconfig.json` | Strict + `noUncheckedIndexedAccess` + `@/*` path alias |

## Design tokens

Defined in `app/globals.css` using OKLCH for consistent perceptual lightness.

Surfaces: `background`, `foreground`, `card`, `popover`, `muted`, `accent`, `secondary`, `primary`, `sidebar`.
Semantic: `success`, `warning`, `info`, `destructive`.
Controls: `border`, `input`, `ring`.
Radii: `sm` / `md` / `lg` / `xl` derived from `--radius: 0.625rem`.
Chart palette: `chart-1`..`chart-5` reserved for Chunk 13 dashboards.

All tokens override cleanly under `.dark` — no component code needs dark-mode branching.

## Accessibility baseline (already wired)

- `:focus-visible` ring on every interactive element via Tailwind utility.
- `prefers-reduced-motion` respected in `@layer base`.
- `suppressHydrationWarning` on `<html>` for theme class swap.
- Theme toggle has mounted-guard to avoid hydration mismatch.

## Security headers

Set globally in `next.config.ts`:
HSTS · X-Frame-Options DENY · X-Content-Type-Options nosniff · Referrer-Policy strict-origin-when-cross-origin · Permissions-Policy (camera/mic/geo/interest-cohort disabled).

CSP is intentionally **not** set yet — it ships in Chunk 16 after all asset sources (Monaco workers, Auth0 callbacks, SSE endpoint) are known.

## Acceptance checklist (verify before moving to Chunk 2)

- [ ] `pnpm install` succeeds.
- [ ] `pnpm dev` boots; `/` renders all four cards.
- [ ] Theme toggle flips light ↔ dark; all swatches re-color; no flash of unstyled content.
- [ ] Tabbing across buttons and toggle shows a visible focus ring.
- [ ] `pnpm typecheck` clean.
- [ ] `pnpm lint` clean (0 warnings).
- [ ] `pnpm build` passes.
- [ ] Lighthouse on `/` ≥ 90 Performance, 100 A11y, 100 Best Practices.
- [ ] Dev tools: response headers include `Strict-Transport-Security`, `X-Frame-Options: DENY`, `Permissions-Policy`.

## One-time local bootstrap

```bash
# Install pnpm if needed:  npm i -g pnpm
pnpm install

# Optional: enable git hooks (from repo root after first commit)
pnpm --filter @sdlc/web exec husky init
```

## What shipped in Chunk 2

Full shadcn primitive set installed manually (not via CLI — code is owned here):

**Form primitives:** `Input`, `Textarea`, `Label`, `Select`, `Checkbox`, `Switch`, `RadioGroup`, `Form` (react-hook-form + Zod).

**Overlays:** `Dialog`, `Sheet` (side), `Drawer` (bottom, vaul), `Popover`, `Tooltip`, `DropdownMenu`, `ContextMenu`, `Command` (cmd+k, via cmdk).

**Display & navigation:** `Alert`, `Badge`, `Avatar`, `Separator`, `ScrollArea`, `Skeleton`, `Progress`, `Tabs`, `Breadcrumb`, `Kbd`, `Card` (from Chunk 1).

**Data:** `Table` (primitives) + `DataTable` (TanStack Table wrapper, sorting/filtering/pagination opt-in).

**App-specific composites:**
- `StatusBadge` — canonical run/artifact statuses (`draft`, `queued`, `running`, `awaiting_approval`, `approved`, `rejected`, `failed`, `merged`, `paused`). Status keys are the single source of truth shared with backend schemas.
- `CostBadge` — tokens + USD, inline or card density, tooltip breakdown of in/out tokens.
- `EmptyState`, `ErrorState`, `LoadingState` — mandatory for every list/table/chart/editor per cross-chunk conventions.
- `Icon` — local SVG wrapper (for brand marks, agent glyphs, connector logos not in lucide).

**Storybook 8** configured with `@storybook/nextjs`, a11y addon, themes addon (light/dark toggle), interactions, Chromatic visual-regression hook.

Consolidated story files (one per category, not one per component) under `stories/`:
`Buttons`, `Inputs` (incl. RHF+Zod form demo), `Overlays`, `Display`, `DataTable`, `StatusBadge`, `States`.

**Providers** updated: `TooltipProvider` wraps the tree so tooltips anywhere in the app "just work".

## Acceptance (Chunk 2)

- [ ] `pnpm install` picks up new deps (vaul, cmdk, react-hook-form, zod, @tanstack/react-table, all @radix-ui/react-* primitives, storybook 8).
- [ ] `pnpm storybook` serves on :6006 with the theme switcher in the toolbar.
- [ ] Every story renders cleanly in both light and dark.
- [ ] Zod form demo (`Inputs` story → "Form (RHF + Zod)") shows validation errors inline.
- [ ] `DataTable` story sorts by column click, filters via column filters.
- [ ] `CommandDialog` example opens and the list filters as you type.
- [ ] A11y addon reports 0 critical violations across all primitives.
- [ ] `pnpm typecheck`, `pnpm lint`, `pnpm build`, `pnpm build-storybook` all clean.

## What shipped in Chunk 3

**Route groups** — `app/(auth)` (public) and `app/(app)` (protected). `middleware.ts` redirects unauthenticated traffic on `(app)/*` to `/login?from=<origin>`. The check is a cookie stub (`sdlc_session`) that Chunk 4 replaces with Auth0 session verification.

**App shell (`components/app/app-shell.tsx`)** composes:
- `Sidebar` — collapsible (240 px ↔ 64 px), persisted via Zustand `persist` to `localStorage`. Tooltips appear when collapsed.
- `MobileSidebar` — Sheet-based drawer on <1024 px, triggered from the top bar. Auto-closes on route change.
- `WorkspaceIdentity` — read-only sidebar header naming the active Business Unit and its organization. Replaced a switcher dropdown: which unit you act in follows from your bindings, not from a control in the chrome.
- `TopBar` — sticky, backdrop-blur, carries breadcrumbs + global search trigger + notifications bell + theme toggle + user menu.
- `Breadcrumbs` — derived from the pathname via `lib/nav.ts` label map; dynamic segments fall back to the raw value (Chunk 5 adds data-driven resolution).
- `CommandPalette` — cmd+k / ctrl+k opens it; arrow-key nav; groups for Navigate / Actions / Theme. Also binds cmd+b to toggle sidebar.
- `NotificationsBell`, `UserMenu`, `GlobalSearchTrigger`, `Kbd` shortcut hints.

**State:** `stores/ui-store.ts` — Zustand store for sidebar collapse, mobile sheet, command-palette open. Only `sidebarCollapsed` persists.

**Pages wired (all placeholders, real versions come later):**
- `/` → redirect to `/projects`.
- `/login` → mock sign-in button that plants the session cookie.
- `/projects`, `/projects/[id]`, `/projects/[id]/requirements`, `/integrations`, `/audit`, `/settings` — shell-ready stubs so navigation feels real.
- `/playground` — the Chunk-1 design-tokens demo, protected under the shell.

**Accessibility:** proper landmark regions (`<aside aria-label="Primary">`, `<header aria-label="Page header">`, `<main id="main" tabIndex="-1">`), `aria-current="page"` on active nav links, `aria-keyshortcuts` on toggles, visible focus rings carried over from Chunk 1.

## Acceptance (Chunk 3)

- [ ] `pnpm dev` and visit `/` — redirected to `/login`.
- [ ] Click "Continue as demo user" — land on `/projects` with full shell.
- [ ] Click sidebar links — Integrations / Audit log / Settings / Projects all navigate, active state flips, breadcrumbs update.
- [ ] Collapse sidebar (footer button or ⌘B) — icons-only, tooltips on hover, state survives reload.
- [ ] Resize <1024 px — sidebar disappears, hamburger appears in top bar, Sheet opens on tap.
- [ ] Press ⌘K / Ctrl+K — palette opens, type "audit", Enter → navigate to audit.
- [ ] Tab from top of page — focus moves through sidebar → workspace switcher → nav → topbar search → bell → theme → user menu, every stop has a visible ring.
- [ ] Dark-mode toggle still works from the topbar.
- [ ] `pnpm typecheck` clean.

## What shipped in Chunk 4

**Two auth modes**, toggled by `NEXT_PUBLIC_AUTH_MODE`:
- `mock` (default) — base64-JSON cookie with a role picker on `/login`. Ships so UI dev is never blocked.
- `auth0` — `@auth0/nextjs-auth0` v4 with `Auth0Client` mounted via middleware. Copy `.env.example` → `.env`, fill AUTH0_* vars.

**Auth module** — `lib/auth/`:
- `types.ts` — `Role` (`admin` / `member` / `viewer`), 13 `Capability` strings, `Session`.
- `capabilities.ts` — **authoritative** role → capability matrix + `can()` pure function.
- `mode.ts` — `isMockAuth` / `isAuth0` flags.
- `mock.ts` — `buildMockSession(role)`, `encodeSession` / `decodeSession` (base64-url, Edge-safe).
- `auth0.ts` — lazy `Auth0Client` factory; throws only if called without env vars.
- `session.ts` — unified `getSession()` server helper; maps Auth0 claims → canonical `Session`.

**Routes / pages:**
- `middleware.ts` — branches by mode; mock-mode cookie gate, or `auth0.middleware()` delegation for `/auth/*` and protected pages.
- `app/(auth)/login/page.tsx` — SSO button in auth0 mode, role selector panel in mock mode.
- `app/api/auth/mock/signin` — POST with `role` + `from`, plants session cookie, 303 to `from`.
- `app/api/auth/mock/logout` — clears cookie, returns to `/login`.
- `app/(app)/layout.tsx` — fetches session server-side, wraps children in `<SessionProvider>`.

**Client hooks + components:**
- `useSession()` / `useSession({ required: true })` — returns `Session | null` (or throws if required and missing).
- `useCan(capability)` — boolean check against the role matrix.
- `<RequireRole role="admin">` / `<RequireRole capability="audit:view">` — UI gate with optional `fallback`.

**Shell now session-aware:**
- `UserMenu` — real name/email/avatar/role; logout link dispatches to mock or Auth0 endpoint by mode.
- `WorkspaceIdentity` — reads tenant from session.
- `Sidebar` + `MobileSidebar` — filter nav items by `requireRole`. Audit log disappears for `member` / `viewer`.
- `CommandPalette` — filters entries by `requireCapability` (audit, new project hidden for viewers).
- `Projects` page — demos `<RequireRole>` and `useCan` (New project button swaps to disabled for viewers).

**Quick role test (mock mode):**
1. `/login` → pick "Viewer" → Continue.
2. Sidebar shows no Audit log. Projects page shows disabled New-project button. ⌘K menu hides the same entries.
3. Sign out → `/login` → pick "Admin" → all entries return.

## Acceptance (Chunk 4)

- [ ] `.env.example` copied (or mock mode used). Default boot has `NEXT_PUBLIC_AUTH_MODE=mock`.
- [ ] Unauth'd → redirect to `/login`.
- [ ] `/login` shows 3-role radio panel in mock mode; SSO button when `NEXT_PUBLIC_AUTH_MODE=auth0`.
- [ ] Signing in as `viewer`: no Audit log in sidebar, disabled New-project, no `audit:view` / `project:create` in palette.
- [ ] Signing in as `admin`: all nav + all palette entries.
- [ ] User menu shows real session fields; Sign out clears cookie and returns to `/login`.
- [ ] `pnpm typecheck` clean.

## What shipped in Chunk 5

**Zod schemas = the backend contract** — `lib/schemas/`:

- **`ids.ts`** — branded types (`ProjectId`, `RunId`, `ArtifactId`, …). Compile-time guarantee you can't pass the wrong ID.
- **`enums.ts`** — `Role`, `Status`, `AgentType`, `Phase`, `ArtifactType`, `ConnectorKind`, `AuditAction`, `LlmProvider`, `ModelTier`. `Status` values are exactly the keys `StatusBadge` renders — change them only here.
- **`primitives.ts`** — `Timestamp`, `Cost`, `Pagination`, `ApiError`, `paginated(item)` helper.
- **Entities** — `Tenant`, `User`/`UserRef`, `Project`/`ProjectPolicy`/`AgentModelBinding`, `Run`, `Step`, `Artifact` (+ typed `ArtifactBody` discriminated union for Story/Mermaid/OpenAPI/DDL/ADR/PR/TestSet), `Connector`/`Capability`, `ApprovalEvent`/`ApprovalSubmitInput`, `AuditEvent`, `Notification`.
- **`stream.ts`** — SSE event discriminated union: `run.started`, `step.progress`, `step.output.delta`, `artifact.updated`, `hitl.pending`, `cost.update`, `guardrail.hit`, `run.completed`.
- **`index.ts`** — barrel export; import everything from `@/lib/schemas`.

**Typed API client** — `lib/api/`:

- **`client.ts`** — `api(path, { schema, query, body, method })`. Validates every response against its Zod schema; normalises errors as `ApiRequestError(status, code, requestId?, details?)`. Single place to add Auth0 bearer + retries + tracing.
- **`query-keys.ts`** — `qk.projects.list()`, `qk.runs.detail(id)`, `qk.audit.list(filters)` … a flat factory keeps cache invalidation hygienic.
- Resource modules: `projects`, `runs`, `artifacts`, `connectors`, `audit`, `stream` (SSE subscribe helper).

**MSW mocks** — `mocks/`:

- **`fixtures.ts`** — 5 projects (1 archived), 20 runs across them, artifact samples (story / C4 diagram / PR), 4 connectors (3 installed, 1 disconnected), 24 audit events.
- **`handlers.ts`** — 14 routes covering projects, runs, approvals, artifacts, connectors, audit, and the SSE stream. Latency is toggled via `NEXT_PUBLIC_MOCK_LATENCY_MS` (default 120ms) so you can exercise loading states.
- **`sse.ts`** — scripted run lifecycle (run.started → 5 steps with streaming deltas + cost updates → artifact.updated → hitl.pending) via `ReadableStream`.
- **`browser.ts`** / **`node.ts`** — worker + server setup. `pnpm msw:init` wrote `public/mockServiceWorker.js` (committed).

**MSW boot** — `components/mocks/msw-init.tsx` wraps the `(app)` children. Only runs in `NODE_ENV=development` and honours `NEXT_PUBLIC_API_MOCKS=off` to target a real backend.

**Projects page now hits the API** — first proof-of-life that the chain *query → api client → fetch → service worker → handler → fixture → Zod parse → UI* works end-to-end with loading / error / empty states. A reload hammers `/api/projects` — visible in DevTools Network.

## Acceptance (Chunk 5)

- [ ] `pnpm install` + `pnpm msw:init` — `public/mockServiceWorker.js` exists.
- [ ] `pnpm dev` → `/projects` — 4 projects render, network tab shows `GET /api/projects` handled by the MSW worker.
- [ ] Bump `NEXT_PUBLIC_MOCK_LATENCY_MS=1500` in `.env` → reload — skeleton `LoadingState` visible for ~1.5s.
- [ ] `NEXT_PUBLIC_API_MOCKS=off` → reload — Projects page shows the `ErrorState` with retry (real backend not up yet).
- [ ] `fetch /api/runs/run_2140/stream` from the console — SSE frames arrive over 10s.
- [ ] `pnpm typecheck` clean. `@/lib/schemas` is importable anywhere.

## Not yet included (by design)

These ship in later chunks, not here:
- Auth0 + protected routes → **Chunk 4**
- API client, schemas, MSW → **Chunk 5**
- Composite components (ApprovalCard, DiffViewer, AgentChatDrawer, …) → **Chunk 6**
- CSP, Playwright E2E, bundle analyzer → **Chunk 16**

## Gotchas

- **Tailwind v4 is CSS-first.** Don't add a `tailwind.config.ts` — tokens live in `app/globals.css` under `@theme` / `@theme inline`. `components.json` intentionally has `tailwind.config: ""`.
- **`tailwind-merge` v3** is required for Tailwind v4 class handling — do not downgrade.
- **React 19** requires `@types/react@^19` and Radix UI v1.1+. Older Radix versions produce ref-forwarding warnings.
- **Husky install** is delayed to `prepare` — safe even in sandboxed CI.
