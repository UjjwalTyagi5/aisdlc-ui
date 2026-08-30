"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Loader2,
  Search,
  Unplug,
} from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { listIntegrationAccess } from "@/lib/api/integration-access";
import { AddMcpServerDialog } from "@/components/app/add-mcp-server-dialog";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import type { RaiseRequestPrefill } from "@/components/requests/raise-request-dialog";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useRawSession } from "@/components/auth/session-provider";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import {
  disconnectConnector,
  listConnectorGrants,
  listConnectors,
  setConnectorCredentials,
} from "@/lib/api/connectors";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import { onboardingScopeFor } from "@/lib/integrations/manage-scope";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { qk } from "@/lib/api/query-keys";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import type { Connector, ConnectorKind } from "@/lib/schemas";

const KIND_LABEL = CONNECTOR_KIND_LABEL;

/** Brand-colored monogram tile per provider (short mark + official brand color). */
const KIND_BRAND: Record<ConnectorKind, { mark: string; bg: string }> = {
  jira: { mark: "J", bg: "#0052CC" }, // Atlassian blue
  azure_devops: { mark: "AB", bg: "#0078D7" }, // Azure Boards blue
  github: { mark: "GH", bg: "#1F2328" }, // GitHub near-black
  azure_repos: { mark: "AR", bg: "#C8511B" }, // Azure Repos orange
  github_actions: { mark: "GA", bg: "#2088FF" }, // GitHub Actions blue
  slack: { mark: "S", bg: "#4A154B" }, // Slack aubergine
  ms_teams: { mark: "T", bg: "#4B53BC" }, // Teams indigo
  sharepoint: { mark: "SP", bg: "#038387" }, // SharePoint teal
  figma: { mark: "F", bg: "#1E1E1E" }, // Figma near-black (its 5-colour mark has no single brand colour)
  confluence: { mark: "C", bg: "#1868DB" }, // Confluence blue (Atlassian family, distinct from Jira's)
  sonarqube: { mark: "SQ", bg: "#4E9BCD" }, // SonarQube blue
  sso_okta: { mark: "OK", bg: "#007DC1" }, // Okta blue
  sso_entra: { mark: "ME", bg: "#0A66C2" }, // Microsoft Entra blue
};

/** Real brand logos (in public/brand). "cover-left" crops a wordmark to its icon. */
const KIND_LOGO: Partial<Record<ConnectorKind, { src: string; fit: "contain" | "cover-left" }>> = {
  azure_devops: { src: "/brand/azure.png", fit: "contain" },
  azure_repos: { src: "/brand/azure.png", fit: "contain" },
  github: { src: "/brand/github.svg", fit: "contain" },
  github_actions: { src: "/brand/github.svg", fit: "contain" },
  slack: { src: "/brand/slack.png", fit: "contain" },
  jira: { src: "/brand/jira.png", fit: "cover-left" },
  ms_teams: { src: "/brand/msteams.svg", fit: "contain" },
  sharepoint: { src: "/brand/sharepoint.svg", fit: "contain" },
  figma: { src: "/brand/figma.svg", fit: "contain" },
};

/** Shared gradient for primary CTAs — the app's brand-gradient button. */
const PRIMARY_CTA =
  "from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]";

/** A neutral monogram for integrations with no vendor brand of their own —
 *  MCP servers are named by whoever registered them. */
function GenericGlyph({ mark }: { mark: string }) {
  return (
    <div
      aria-hidden
      className="border-line-soft bg-surface-2 text-muted-foreground grid size-10 shrink-0 place-items-center rounded-lg border font-mono text-[13px] font-semibold"
    >
      {mark}
    </div>
  );
}

function KindGlyph({ kind, size = 10 }: { kind: ConnectorKind; size?: 10 | 12 }): React.ReactElement {
  const dim = size === 12 ? "size-12" : "size-10";
  const logo = KIND_LOGO[kind];
  if (logo) {
    return (
      <div
        className={cn(
          "border-line-soft grid shrink-0 place-items-center overflow-hidden rounded-lg border bg-white shadow-[0_1px_0_oklch(1_0_0_/_0.22)_inset,0_2px_8px_-3px_oklch(0_0_0_/_0.5)]",
          dim,
        )}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- small static brand asset; <img> renders SVG+PNG uniformly without next/image SVG config */}
        <img
          src={logo.src}
          alt=""
          aria-hidden
          className={cn(
            "size-full",
            logo.fit === "cover-left" ? "object-cover object-left" : "object-contain p-1.5",
          )}
        />
      </div>
    );
  }
  // Fallback: brand-colored monogram tile (Okta, Entra — no logo file).
  const { mark, bg } = KIND_BRAND[kind];
  return (
    <div
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center rounded-lg font-mono text-[13px] font-bold tracking-tight text-white shadow-[0_1px_0_oklch(1_0_0_/_0.22)_inset,0_2px_8px_-3px_oklch(0_0_0_/_0.5)]",
        dim,
      )}
      style={{ backgroundColor: bg }}
    >
      {mark}
    </div>
  );
}

/** Provider kinds that have live validation deferred — show "pending" annotation. */

/** Provider kinds connected by pasting a credential (PAT / API token) instead of OAuth. */

/** Connector categories — the page is grouped by what each integration is FOR.
 *  Azure Repos is intentionally absent: Azure DevOps is now one consolidated tile
 *  covering boards, repos, and CI/CD (one credential connects all three). */
const CATEGORIES: { id: string; title: string; blurb: string; kinds: ConnectorKind[] }[] = [
  {
    id: "work-tracking",
    title: "Work tracking & boards",
    blurb: "Pull epics and user stories into the Requirements agent.",
    kinds: ["jira", "azure_devops"],
  },
  {
    id: "source-control",
    title: "Source control",
    blurb: "Read repositories, branch, and open pull requests.",
    kinds: ["github"],
  },
  {
    id: "deployment",
    title: "Deployment & CI/CD",
    blurb: "Trigger pipelines and read deployment status for the Deployment agent.",
    kinds: ["github_actions"],
  },
  {
    id: "quality",
    title: "Code quality & security",
    blurb: "Read live quality-gate status and issues, and triage them, for the Testing and Review agents.",
    kinds: ["sonarqube"],
  },
  {
    id: "notifications",
    title: "Notifications & approvals",
    blurb: "Human-in-the-loop alerts and gate approvals.",
    kinds: ["slack", "ms_teams"],
  },
  {
    id: "documents",
    title: "Documents & knowledge",
    blurb: "Read specifications and file generated documentation where the business looks for it.",
    kinds: ["sharepoint", "confluence"],
  },
  {
    id: "design",
    title: "Design & prototyping",
    blurb: "Ground the Design agent in the screens that were actually drawn.",
    kinds: ["figma"],
  },
];

/** kind → category title, derived from CATEGORIES so the two never drift. */
const KIND_CATEGORY = Object.fromEntries(
  CATEGORIES.flatMap((cat) => cat.kinds.map((k) => [k, cat.title] as const)),
) as Record<ConnectorKind, string>;

/** Every kind the platform offers, in CATEGORIES order — the universe the Org
 *  Admin's grant card lists, and the superset the tiles are filtered from. */
const ALL_KINDS: ConnectorKind[] = CATEGORIES.flatMap((cat) => cat.kinds);

/** Override for the top-right tile chip when a connector spans multiple purposes.
 *  Azure DevOps is one connection that does all three — the chip says so. */
const KIND_CAPABILITY_CHIP: Partial<Record<ConnectorKind, string>> = {
  azure_devops: "Boards · Repos · CI/CD",
};

/** The chip text shown top-right on an available tile (capability override → category). */
function chipLabel(kind: ConnectorKind): string {
  return KIND_CAPABILITY_CHIP[kind] ?? KIND_CATEGORY[kind];
}

/** A connector is "connected" only when installed AND its health isn't disconnected.
 * The global env probe can mark a connector installed while it's actually disconnected,
 * so this is the single source of truth for status pills, counts, and card actions. */
export default function IntegrationsPage() {
  const queryClient = useQueryClient();
  const session = useRawSession();
  const role = effectivePlatformRole(session);

  // The units this viewer is bound to. No id is passed to either query: the
  // server unions across exactly those units and drops anything outside them,
  // which is both the correct answer for someone in two units and the same
  // answer as before for someone in one. Passing an "active" unit here was
  // what made the page arbitrary — see hooks/use-scoped-business-units.ts.
  const { units: scopedUnits } = useScopedBusinessUnits();

  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(null),
    queryFn: () => listConnectors(),
    staleTime: 0,
  });

  // Which kinds this viewer may touch at all. An Org Admin writes the policy,
  // so they see the whole catalogue and edit it below; anyone else sees only
  // what their units were granted — a kind they weren't granted is absent, not
  // disabled, because a tile with a permanently dead button is worse than no
  // tile at all.
  const isOrgAdmin = role === "org_admin";
  const grantsQ = useQuery({
    queryKey: qk.connectors.grants(null),
    queryFn: () => listConnectorGrants(),
    enabled: !isOrgAdmin,
  });

  const [disconnectFor, setDisconnectFor] = React.useState<Connector | null>(null);
  const [credentialsFor, setCredentialsFor] = React.useState<ConnectorKind | null>(null);
  // Shared by the matrix, the catalogue and the MCP panel — one box, one term,
  // whichever half of the page the answer is in.
  const [query, setQuery] = React.useState("");

  // Access counts for the cards, in one call rather than one per card.
  const accessQ = useQuery({
    queryKey: qk.integrationAccess.list(),
    queryFn: () => listIntegrationAccess(),
  });
  // Keyed by `kind:id` so connectors and MCP servers share one lookup.
  //
  // `grantedUnitCount`, NOT `units.length` — the payload lists every unit as a
  // candidate so the grant picker has something to offer, and counting those
  // told every card "3 business units" whatever its grant actually said.
  const accessByKind = React.useMemo(() => {
    const m = new Map<string, { units: number; projects: number }>();
    for (const r of accessQ.data ?? []) {
      m.set(`${r.kind}:${r.id}`, { units: r.grantedUnitCount, projects: r.projectCount });
    }
    return m;
  }, [accessQ.data]);

  // REMOVED: the on-mount ?connected={kind} success toast. Its only producer was
  // OAuthCallbackHandler, the page the OAuth provider redirected back to, and that
  // page is gone along with the flow. Pasting a credential reports its own result
  // inline, so nothing arrives here needing to be announced after a redirect.

  const disconnectMutation = useMutation({
    mutationFn: (kind: ConnectorKind) => disconnectConnector(kind),
    onSuccess: (c) => {
      toast.success(`${c.name} disconnected`);
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err) =>
      toast.error("Couldn't disconnect", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  if (connectorsQ.isLoading) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <LoadingState variant="list" rows={4} />
      </div>
    );
  }

  // Elevated error state — uses api-error-state on the isError branch
  if (connectorsQ.isError) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ApiErrorState
          title="Couldn't load connectors"
          error={
            connectorsQ.error && "code" in connectorsQ.error && "message" in connectorsQ.error
              ? (connectorsQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(connectorsQ.error && "code" in connectorsQ.error)
              ? connectorsQ.error instanceof Error
                ? connectorsQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => connectorsQ.refetch()}
        />
      </div>
    );
  }

  // Phase 6: page-level connector:view guard — excludes stakeholder (artifact:view only).
  //
  // Every role that may open this page lists `connector:view` explicitly, and
  // admin:* passes via the wildcard. An earlier comment here claimed
  // `connector:manage` implied `connector:view`; it does not — `hasPermission`
  // mirrors the backend's exact membership test — and the Business Unit Admin,
  // who held only the manage half, was bounced off a page its own sidebar link
  // offered. Grant both halves in lib/auth/role-permissions.ts rather than
  // loosening this gate or the primitive behind it.
  if (!hasPermission(session, "connector:view")) {
    return <RestrictedAccess description="Connectors require the connector:view permission." />;
  }

  const connectors = connectorsQ.data ?? [];
  const tenantId = (connectors[0]?.tenantId ?? "") as Connector["tenantId"];

  // The catalog (CATEGORIES) is the source of truth for which tiles exist. The
  // backend list only enumerates connectors it has probed/stored, so a brand-new
  // credential kind (e.g. github_actions) or an un-probed kind (SSO) would be
  // missing. Synthesize a disconnected placeholder for any catalog kind the
  // backend didn't return, so every catalog entry renders as "available".
  // A placeholder is a connector nobody has onboarded yet, so it carries the
  // scope this viewer would onboard it *into* — org-wide for an Org Admin, the
  // active unit for anyone else (PRD §34.3).
  const wouldOnboardAt = onboardingScopeFor(role);
  const byKind = new Map(connectors.map((c) => [c.kind, c] as const));
  // An Org Admin sees the whole catalogue; everyone else only the kinds their
  // unit was granted. Un-granted kinds must not be synthesized as placeholders
  // either — a placeholder is exactly what would put a "Connect" button on a
  // connector the organization never permitted.
  const permittedKinds = isOrgAdmin
    ? new Set<string>(ALL_KINDS)
    : new Set<string>((grantsQ.data ?? []).map((g) => g.kind));
  // EVERY kind is rendered now, granted or not. Hiding the ungranted ones kept
  // a dead "Connect" button off the page, which was right, but it also made
  // "we were never given Slack" indistinguishable from "Slack isn't a thing
  // this platform does" — and the person who needs to tell those apart is
  // exactly the one who would go on to ask for it. An ungranted tile carries a
  // Request access button instead of a Connect one, so the affordance matches
  // the standing.
  const visibleKinds = ALL_KINDS;
  const resolved = visibleKinds.map(
    (k): Connector =>
      byKind.get(k) ?? {
        id: k as Connector["id"],
        tenantId,
        kind: k,
        name: KIND_LABEL[k],
        installed: false,
        health: "disconnected",
        capabilities: [],
        lastCheckedAt: null,
        scope: wouldOnboardAt.scope,
        // A placeholder is a connector nobody has onboarded yet. With one unit
        // it is already known where it would land; with several the answer is
        // "not decided yet", which the credentials dialog asks for.
        workspaceId:
          wouldOnboardAt.scope === "business_unit" && scopedUnits.length === 1
            ? scopedUnits[0]!.id
            : null,
      },
  );

  // Split for the control-panel layout: connected integrations are featured at the
  // top; everything else becomes a quiet "available to connect" grid. Tiles keep
  // CATEGORIES order so the IA (work-tracking → SCM → deployment → ...) is preserved.

  // The catalogue answers to the same search box as the matrix. Counts above
  // stay unfiltered: a total that moved as you typed would read as a filtered
  // total rather than the estate's size.
  const matchesQuery = (c: Connector) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      (KIND_LABEL[c.kind] ?? "").toLowerCase().includes(q) ||
      c.kind.toLowerCase().includes(q) ||
      (c.name ?? "").toLowerCase().includes(q)
    );
  };
  const catalogueConnectors = resolved.filter(matchesQuery);

  const mcpRows = (accessQ.data ?? [])
    .filter((r) => r.kind === "mcp")
    .filter((r) => {
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return r.name.toLowerCase().includes(q) || (r.description ?? "").toLowerCase().includes(q);
    });

  // The catalogue no longer splits by onboarding level. That split existed to
  // show the cascade, and the access matrix above now shows it properly — per
  // integration, per unit, per project — so repeating it as two headings said
  // less in more space.

  return (
    <div className="w-full space-y-10 p-4 md:px-10 md:py-8">
      {/* Editorial page header */}
      <header
        className="flex flex-col items-start gap-1"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <PageTitle>Integrations</PageTitle>
      </header>

      {/* One search across both halves of the page: the catalogue you onboard
          from, and the matrix of who holds what. Splitting it into two boxes
          would ask the reader to know which half their answer is in. */}
      <div className="border-line-soft bg-surface-1 flex max-w-md items-center gap-2 rounded-lg border px-2.5">
        <Search className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search integrations, ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} or projects…`}
          aria-label={`Search integrations, ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} or projects`}
          className="h-9 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery("")}
            className="text-muted-foreground hover:text-foreground shrink-0 font-mono text-[10.5px] transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      {/* Keyed on the GRANT count, not on how many tiles render — every kind
          is on the page now, so "nothing rendered" stopped being the signal
          for "you hold nothing". Each tile carries its own Request access
          button, so this says what the state IS and leaves the asking to them. */}
      {!isOrgAdmin && permittedKinds.size === 0 && !grantsQ.isLoading && (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            Your Organization Admin hasn&apos;t permitted any connectors for this{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} yet. Everything below is the catalogue — request
            the ones your teams need, and nothing can be connected until they&apos;re granted.
          </p>
        </div>
      )}

      {/* ── Connectors ──────────────────────────────────────────────────────
          ONE grid, not three lists. Connected and available differ by a health
          pill and a call to action, which the card already carries — splitting
          them into sections made you look in two places for one vendor, and
          the access matrix made a third. Everything an integration needs doing
          to it now lives on its own screen, one click in. */}
      <section aria-labelledby="connectors-heading" className="space-y-4">
        <SectionLabel
          id="connectors-heading"
          eyebrow="Connectors"
          title="Connectors"
          blurb={
            isOrgAdmin
              ? `Open one to see which ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} hold it, which projects use it, and to revoke either.`
              : `Solid tiles are yours to use. Dashed ones exist but weren't granted to you — request those.`
          }
        />
        {catalogueConnectors.length === 0 ? (
          <Card className="text-muted-foreground p-8 text-center text-sm">
            No connector matches &ldquo;{query.trim()}&rdquo;.
          </Card>
        ) : (
          <ul className="grid items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {catalogueConnectors.map((c) => (
              <IntegrationCard
                key={c.id}
                href={`/integrations/${encodeURIComponent(c.kind)}`}
                label={KIND_LABEL[c.kind]}
                category={chipLabel(c.kind)}
                glyph={<KindGlyph kind={c.kind} />}
                access={accessByKind.get(`connector:${c.kind}`)}
                granted={permittedKinds.has(c.kind)}
                showUnitCount={isOrgAdmin}
                requestPrefill={{
                  type: "connector_access",
                  title: `${KIND_LABEL[c.kind]} access`,
                  description: `Requesting ${KIND_LABEL[c.kind]} for our work. It isn't granted to us today.`,
                }}
              />
            ))}
          </ul>
        )}
      </section>

      {/* MCP servers, in the same grid shape. They are governed identically —
          granted to units, consumed by projects — so a reader scanning for
          "who can reach Postgres" should not have to learn a second layout to
          find it. */}
      <section aria-labelledby="mcp-heading" className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <SectionLabel
            id="mcp-heading"
            eyebrow="MCP"
            title="MCP servers"
            blurb="Tool servers your projects' agents can call. Open one to see who holds it."
          />
          {/* Connectors need no equivalent — their kinds ship in the catalogue.
              An MCP server is whatever someone stood up, so it does not exist
              until it is named here.

              And because it does not exist until then, there is no dashed tile
              to request from either: the ask is "stand this one up", which is
              a description, not a pick from a list. Hence a section-level
              button for everyone who cannot add one themselves. */}
          {isOrgAdmin ? (
            <AddMcpServerDialog />
          ) : (
            <RequestAccessButton
              label="Request an MCP server"
              prefill={{
                type: "mcp_server",
                title: "New MCP server",
                description:
                  "Which server, where it runs, and what our agents would call it for:",
              }}
            />
          )}
        </div>
        {mcpRows.length === 0 ? (
          <Card className="text-muted-foreground p-8 text-center text-sm">
            {query.trim()
              ? `No MCP server matches "${query.trim()}".`
              : "No MCP server is registered yet."}
          </Card>
        ) : (
          <ul className="grid items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {mcpRows.map((r) => (
              <IntegrationCard
                key={r.id}
                href={`/integrations/${encodeURIComponent(r.id)}`}
                label={r.name}
                category="MCP server"
                glyph={<GenericGlyph mark={r.name.slice(0, 2).toUpperCase()} />}
                access={accessByKind.get(`mcp:${r.id}`)}
                showUnitCount={isOrgAdmin}
              />
            ))}
          </ul>
        )}
      </section>


      {/* Disconnect confirm */}
      <DisconnectConfirm
        connector={disconnectFor}
        onClose={() => setDisconnectFor(null)}
        onConfirm={(c) => {
          setDisconnectFor(null);
          disconnectMutation.mutate(c.kind);
        }}
      />

      {/* Credential entry (ADO PAT / Jira API token) */}
      <CredentialsDialog
        kind={credentialsFor}
        targetUnits={isOrgAdmin ? [] : scopedUnits}
        onClose={() => setCredentialsFor(null)}
        onSaved={() => queryClient.invalidateQueries({ queryKey: ["connectors"] })}
      />
    </div>
  );
}

// ───────── Section label ─────────

function SectionLabel({
  id,
  eyebrow,
  title,
  blurb,
}: {
  id: string;
  eyebrow: string;
  title: string;
  blurb?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-muted-foreground font-mono text-[10px] tracking-[0.16em] uppercase">
        {eyebrow}
      </span>
      <h2 id={id} className="font-display text-xl font-bold tracking-[-0.015em]">
        {title}
      </h2>
      {blurb && <p className="text-muted-foreground text-[13px]">{blurb}</p>}
    </div>
  );
}

// ───────── Integration card ─────────

/**
 * One card per integration, and the whole card opens its screen.
 *
 * The page used to be three lists — available, connected org-wide, connected
 * in a unit — plus a separate access matrix. Four places to look for one
 * integration. A card carries what you scan for (what it is, whether it works,
 * how far it reaches) and everything you act on lives one click deeper, on the
 * screen that has the context for it.
 */
function IntegrationCard({
  href,
  label,
  category,
  glyph,
  access,
  granted = true,
  requestPrefill,
  showUnitCount = false,
}: {
  href: string;
  label: string;
  category: string;
  glyph: React.ReactNode;
  access?: { units: number; projects: number };
  /** False when the viewer's scope holds no grant for this kind. */
  granted?: boolean;
  /** Seeds the request raised from an ungranted tile. */
  requestPrefill?: RaiseRequestPrefill;
  /**
   * Show the "N business units" clause. Only worth stating for someone who
   * oversees more than one — an Org Admin, comparing units against each
   * other. Every other viewer's `access.units` is capped at the one unit
   * they administer or belong to (grantedUnitCount is scoped to what the
   * VIEWER can see), so the count is always 1 and says nothing a Business
   * Unit or Project Admin doesn't already know about their own unit.
   */
  showUnitCount?: boolean;
}) {
  return (
    <li className="h-full">
      {/* Stretched link, not an onClick: a div with a handler is invisible to
          a keyboard and cannot be opened in a new tab, which is what people do
          with a grid of things to compare. The CTA below sits above it. */}
      <Card
        className={cn(
          "border-line-soft bg-panel-elevated/70 focus-within:ring-ring relative flex h-full flex-col gap-3 rounded-2xl border p-4 shadow-[0_1px_0_oklch(1_0_0_/_0.03)_inset] transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-[0_14px_30px_-16px_oklch(0_0_0_/_0.5)] hover:ring-1 hover:ring-brand-bright/20 focus-within:ring-2",
          // Dimmed, not disabled. The tile still opens — seeing WHO does hold a
          // connector is half the answer to whether asking for it is reasonable.
          !granted && "border-dashed opacity-70 hover:opacity-100",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          {glyph}
          <span className="text-muted-foreground/80 bg-muted/40 rounded-full px-2 py-0.5 font-mono text-[9.5px] tracking-[0.08em] uppercase">
            {category}
          </span>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-display truncate text-[14.5px] font-bold tracking-[-0.01em]">
              {/* NOT a link when ungranted. The detail page answers "which
                  business units hold this", which is Org/BU Admin territory —
                  a Project Admin following it lands on Access restricted. A
                  tile whose only affordance leads to a wall is worse than one
                  that just states its status and offers the ask. */}
              {granted ? (
                <Link
                  href={href}
                  className="rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
                >
                  {label}
                </Link>
              ) : (
                label
              )}
            </h3>
          </div>
          {/* NO tagline. "Read issues + write sub-tasks" explains what Jira is,
              which is useful exactly once and then sits on the card forever for
              the people who read it daily. The reach below is the fact that
              changes, and the only one worth the row. */}
          {granted && access && (
            <p className="text-muted-foreground mt-2 font-mono text-[11px]">
              {showUnitCount && (
                <>
                  {access.units} {access.units === 1 ? "business unit" : "business units"}
                  {" · "}
                </>
              )}
              {access.projects} {access.projects === 1 ? "project" : "projects"}
            </p>
          )}
          {!granted && (
            <p className="text-muted-foreground mt-2 font-mono text-[11px]">
              Not granted to you
            </p>
          )}
        </div>

        {!granted && requestPrefill && (
          <RequestAccessButton prefill={requestPrefill} className="w-full justify-center" />
        )}

        {/* NO connect / credential action.
            Neither admin tier ever authenticates to a connector: the
            organization decides which integrations exist and who may use them,
            and each PROJECT supplies the identity it calls with
            (/projects/[id]/integrations). An "Add credentials" button here
            offered a step that belongs to somebody else. */}
      </Card>
    </li>
  );
}

// ───────── Disconnect confirm ─────────

function DisconnectConfirm({
  connector,
  onClose,
  onConfirm,
}: {
  connector: Connector | null;
  onClose: () => void;
  onConfirm: (c: Connector) => void;
}) {
  const open = !!connector;
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <Unplug className="text-destructive size-5" aria-hidden />
            Disconnect {connector?.name}?
          </DialogTitle>
          <DialogDescription>
            Your credentials will be deleted from the secrets vault. Runs that use this connector
            will fail until you reconnect. Webhooks will stop flowing in.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="border-line-soft">
            Keep Connected
          </Button>
          <Button variant="destructive" onClick={() => connector && onConfirm(connector)}>
            Disconnect
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ───────── Credential entry (ADO PAT / Jira API token) ─────────

function CredentialsDialog({
  kind,
  targetUnits,
  onClose,
  onSaved,
}: {
  kind: ConnectorKind | null;
  /** The units the viewer may onboard into. Empty for an Org Admin, whose
   *  connections are org-wide; one entry needs no question; several make the
   *  target a real choice, asked here rather than assumed. */
  targetUnits: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const open = !!kind;
  const isJira = kind === "jira";
  const isConfluence = kind === "confluence";
  const isSonarQube = kind === "sonarqube";
  const isGithubActions = kind === "github_actions";
  const isFigma = kind === "figma";
  // GitHub and Slack are pasteable now. Both used to be OAuth-only, so this dialog
  // never had a shape for them; with the platform OAuth app gone, a tenant supplies
  // its own GitHub App (id + private key + installation) or its own Slack bot token.
  const isGithub = kind === "github";
  const isSlack = kind === "slack";
  const mustChooseUnit = targetUnits.length > 1;
  const [unitId, setUnitId] = React.useState("");
  const targetUnitId = unitId || targetUnits[0]?.id || "";

  const [orgUrl, setOrgUrl] = React.useState("");
  const [pat, setPat] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [apiToken, setApiToken] = React.useState("");
  const [ghToken, setGhToken] = React.useState("");
  const [ghOwner, setGhOwner] = React.useState("");
  const [figmaToken, setFigmaToken] = React.useState("");
  const [figmaFileUrl, setFigmaFileUrl] = React.useState("");
  const [confluenceSpaceKey, setConfluenceSpaceKey] = React.useState("");
  const [ghAppId, setGhAppId] = React.useState("");
  const [ghAppKey, setGhAppKey] = React.useState("");
  const [ghInstallId, setGhInstallId] = React.useState("");
  const [slackToken, setSlackToken] = React.useState("");
  const [pending, setPending] = React.useState(false);

  // Reset all fields whenever the dialog closes or switches connector.
  React.useEffect(() => {
    if (!open) {
      setOrgUrl("");
      setPat("");
      setBaseUrl("");
      setEmail("");
      setApiToken("");
      setGhToken("");
      setGhOwner("");
      setFigmaToken("");
      setFigmaFileUrl("");
      setConfluenceSpaceKey("");
      setGhAppId("");
      setGhAppKey("");
      setGhInstallId("");
      setSlackToken("");
      setUnitId("");
      setPending(false);
    }
  }, [open]);

  const fieldsValid = isJira || isConfluence
    ? Boolean(baseUrl.trim() && email.trim() && apiToken.trim())
    : isGithubActions
      ? Boolean(ghToken.trim())
      : isFigma
        ? Boolean(figmaToken.trim())
        : isGithub
          ? Boolean(ghAppId.trim() && ghAppKey.trim() && ghInstallId.trim())
          : isSlack
            ? Boolean(slackToken.trim())
            : Boolean(orgUrl.trim() && pat.trim());
  const canSubmit = fieldsValid && (!mustChooseUnit || Boolean(targetUnitId));

  const handleSubmit = async () => {
    if (!kind || !canSubmit || pending) return;
    setPending(true);
    try {
      const fields = isConfluence
        ? {
            base_url: baseUrl.trim(),
            email: email.trim(),
            api_token: apiToken,
            space_key: confluenceSpaceKey.trim() || undefined,
          }
        : isJira
        ? { base_url: baseUrl.trim(), email: email.trim(), api_token: apiToken }
        : isGithubActions
          ? { pat: ghToken, owner: ghOwner.trim() || undefined }
          : isFigma
            ? { pat: figmaToken, file_url: figmaFileUrl.trim() || undefined }
            : isGithub
              ? {
                  github_app_id: ghAppId.trim(),
                  github_app_private_key: ghAppKey,
                  github_app_installation_id: ghInstallId.trim(),
                }
              : isSlack
                ? { pat: slackToken }
                : { org_url: orgUrl.trim(), pat };
      const result = await setConnectorCredentials(kind, {
        ...fields,
        workspaceId: targetUnitId || undefined,
      });
      onSaved();
      if (result.status === "valid") {
        toast.success(`${KIND_LABEL[kind]} connected ✓`);
        onClose();
      } else {
        toast.error("Credentials saved, but verification failed", {
          description: result.error
            ? `Probe error: ${result.error}. Check the values and try again.`
            : "The provider rejected the credentials. Check the values and try again.",
        });
      }
    } catch (err) {
      toast.error("Couldn't save credentials", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !pending && !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">
            {kind ? `Connect ${KIND_LABEL[kind]}` : "Connect"}
          </DialogTitle>
          <DialogDescription>
            {isJira
              ? "Paste your Jira site URL, account email, and an API token. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
              : isConfluence
                ? "Paste your Confluence site URL, account email, and an API token — the same kind of token Jira uses, configured separately here. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
                : isGithubActions
                ? "Paste a GitHub Personal Access Token (scopes: repo, workflow) and, optionally, the owner/org. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
                : isGithub
                  ? "Register a GitHub App in your own organization, then paste its App ID, private key, and the installation ID for this org. The platform no longer runs a shared GitHub App, so the App — and the access it has — is entirely yours. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
                : isSlack
                  ? "Create a Slack app in your own workspace with the chat:write and channels:read scopes, then paste its bot token (xoxb-…). Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
                : isFigma
                  ? "Paste a Figma personal access token (Figma → Settings → Security → Personal access tokens). Read-only: the Design agent reads your screens and never writes to them. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
                  : isSonarQube
                    ? "Paste your SonarQube server URL and a user token (My Account → Security → Generate Token). Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
                    : "Paste your Azure DevOps organization URL and a Personal Access Token — this one connection powers boards, repos, and CI/CD. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Only asked when there is genuinely a choice — a credential lands
              in exactly one unit, and with several bound to the viewer nobody
              should pick for them. */}
          {mustChooseUnit && (
            <div className="space-y-1.5">
              <Label htmlFor="cred-unit">{BUSINESS_UNIT_LABEL}</Label>
              <select
                id="cred-unit"
                value={targetUnitId}
                onChange={(e) => setUnitId(e.target.value)}
                disabled={pending}
                className="border-line-soft bg-surface-1 h-9 w-full rounded-md border px-3 text-[13px]"
              >
                {targetUnits.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
              <p className="text-muted-foreground text-[11px]">
                This connection is stored for the {BUSINESS_UNIT_LABEL.toLowerCase()} you pick and
                is not visible to the others.
              </p>
            </div>
          )}

          {isGithub ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="gh-app-id">App ID</Label>
                <Input
                  id="gh-app-id"
                  value={ghAppId}
                  onChange={(e) => setGhAppId(e.target.value)}
                  placeholder="123456"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gh-install-id">Installation ID</Label>
                <Input
                  id="gh-install-id"
                  value={ghInstallId}
                  onChange={(e) => setGhInstallId(e.target.value)}
                  placeholder="87654321"
                  autoComplete="off"
                />
                <p className="text-muted-foreground text-[11px]">
                  From the URL when you install the App:
                  github.com/settings/installations/<span className="font-mono">&lt;id&gt;</span>.
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gh-app-key">Private key (PEM)</Label>
                <Textarea
                  id="gh-app-key"
                  value={ghAppKey}
                  onChange={(e) => setGhAppKey(e.target.value)}
                  placeholder="-----BEGIN RSA PRIVATE KEY-----"
                  rows={4}
                  autoComplete="off"
                  className="font-mono text-[11px]"
                />
                <p className="text-muted-foreground text-[11px]">
                  This key signs as the App across every repository it is installed on.
                  It is stored in your tenant&apos;s vault and never shown again.
                </p>
              </div>
            </>
          ) : isSlack ? (
            <div className="space-y-1.5">
              <Label htmlFor="slack-token">Bot token</Label>
              <Input
                id="slack-token"
                type="password"
                value={slackToken}
                onChange={(e) => setSlackToken(e.target.value)}
                placeholder="xoxb-••••••••••••"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-[11px]">
                Slack app → OAuth &amp; Permissions → Bot User OAuth Token. Needs the
                chat:write and channels:read scopes.
              </p>
            </div>
          ) : isGithubActions ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="gha-token">Personal Access Token</Label>
                <Input
                  id="gha-token"
                  type="password"
                  value={ghToken}
                  onChange={(e) => setGhToken(e.target.value)}
                  placeholder="ghp_••••••••••••"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gha-owner">
                  Owner / org{" "}
                  <span className="text-muted-foreground/60 normal-case">(optional)</span>
                </Label>
                <Input
                  id="gha-owner"
                  value={ghOwner}
                  onChange={(e) => setGhOwner(e.target.value)}
                  placeholder="acme"
                  autoComplete="off"
                />
              </div>
            </>
          ) : isFigma ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="figma-token">Personal access token</Label>
                <Input
                  id="figma-token"
                  type="password"
                  value={figmaToken}
                  onChange={(e) => setFigmaToken(e.target.value)}
                  placeholder="figd_••••••••••••"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="figma-file-url">
                  Default file{" "}
                  <span className="text-muted-foreground/60 normal-case">(optional)</span>
                </Label>
                <Input
                  id="figma-file-url"
                  value={figmaFileUrl}
                  onChange={(e) => setFigmaFileUrl(e.target.value)}
                  placeholder="https://www.figma.com/design/abc123/Product"
                  autoComplete="off"
                />
                <p className="text-muted-foreground text-[11px]">
                  The file the Design agent reads when nobody names one. It can always be
                  given a different file at the time of asking.
                </p>
              </div>
            </>
          ) : isJira || isConfluence ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="jira-base-url">Site URL</Label>
                <Input
                  id="jira-base-url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://your-org.atlassian.net"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="jira-email">Account email</Label>
                <Input
                  id="jira-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="jira-token">API token</Label>
                <Input
                  id="jira-token"
                  type="password"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  placeholder="••••••••••••"
                  autoComplete="off"
                />
              </div>
              {isConfluence && (
                <div className="space-y-1.5">
                  <Label htmlFor="confluence-space">
                    Default space{" "}
                    <span className="text-muted-foreground/60 normal-case">(optional)</span>
                  </Label>
                  <Input
                    id="confluence-space"
                    value={confluenceSpaceKey}
                    onChange={(e) => setConfluenceSpaceKey(e.target.value)}
                    placeholder="ENG"
                    autoComplete="off"
                  />
                  <p className="text-muted-foreground text-[11px]">
                    The space the Documentation agent files pages into when nobody names
                    one. It can always be given a different space at the time of asking.
                  </p>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="ado-org-url">
                  {isSonarQube ? "Server URL" : "Organization URL"}
                </Label>
                <Input
                  id="ado-org-url"
                  value={orgUrl}
                  onChange={(e) => setOrgUrl(e.target.value)}
                  placeholder={
                    isSonarQube ? "https://sonar.your-company.com" : "https://dev.azure.com/your-org"
                  }
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ado-pat">
                  {isSonarQube ? "User token" : "Personal Access Token"}
                </Label>
                <Input
                  id="ado-pat"
                  type="password"
                  value={pat}
                  onChange={(e) => setPat(e.target.value)}
                  placeholder="••••••••••••"
                  autoComplete="off"
                />
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={onClose}
            disabled={pending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!canSubmit || pending}
            aria-busy={pending}
            className={PRIMARY_CTA}
          >
            {pending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            {pending ? "Verifying…" : "Test & Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
