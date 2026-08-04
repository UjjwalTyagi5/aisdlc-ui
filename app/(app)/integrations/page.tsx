"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Eye,
  KeyRound,
  Loader2,
  Plug,
  ShieldAlert,
  Unplug,
  type LucideIcon,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
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
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { ConnectorGrantsCard } from "@/components/app/connector-grants-card";
import { McpServersPanel } from "@/components/app/mcp-servers-panel";
import { RequireRole } from "@/components/auth/require-role";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useRawSession } from "@/components/auth/session-provider";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  disconnectConnector,
  installConnector,
  listConnectorGrants,
  listConnectors,
  setConnectorCredentials,
} from "@/lib/api/connectors";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import { onboardingScopeFor } from "@/lib/mock/connector-scope";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { qk } from "@/lib/api/query-keys";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import type { Capability, Connector, ConnectorKind } from "@/lib/schemas";

const KIND_LABEL = CONNECTOR_KIND_LABEL;

const KIND_TAGLINE: Record<ConnectorKind, string> = {
  jira: "Read issues + write sub-tasks. Webhook-driven.",
  azure_devops:
    "Boards, Repos & Pipelines — work items, pull requests, and CI/CD from one connection.",
  github: "Read repos + open PRs + post check-runs via a GitHub App.",
  azure_repos: "Read repos + commit + open PRs.",
  github_actions: "Trigger workflows + read run status via a PAT.",
  slack: "HITL notifications + interactive approvals.",
  sso_okta: "SAML + OIDC identity provider.",
  sso_entra: "SAML + OIDC identity provider.",
};

/** Brand-colored monogram tile per provider (short mark + official brand color). */
const KIND_BRAND: Record<ConnectorKind, { mark: string; bg: string }> = {
  jira: { mark: "J", bg: "#0052CC" }, // Atlassian blue
  azure_devops: { mark: "AB", bg: "#0078D7" }, // Azure Boards blue
  github: { mark: "GH", bg: "#1F2328" }, // GitHub near-black
  azure_repos: { mark: "AR", bg: "#C8511B" }, // Azure Repos orange
  github_actions: { mark: "GA", bg: "#2088FF" }, // GitHub Actions blue
  slack: { mark: "S", bg: "#4A154B" }, // Slack aubergine
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
};

/** Shared gradient for primary CTAs — the app's brand-gradient button. */
const PRIMARY_CTA =
  "from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]";

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
const VALIDATION_PENDING_KINDS: ConnectorKind[] = ["github", "azure_repos", "slack"];

/** Provider kinds connected by pasting a credential (PAT / API token) instead of OAuth. */
const CREDENTIAL_KINDS: ConnectorKind[] = ["azure_devops", "jira", "github_actions"];

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
    id: "notifications",
    title: "Notifications & approvals",
    blurb: "Human-in-the-loop alerts and gate approvals.",
    kinds: ["slack"],
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
function isConnected(c: Connector): boolean {
  return c.installed && c.health !== "disconnected";
}

export default function IntegrationsPage() {
  const queryClient = useQueryClient();
  const session = useRawSession();
  const searchParams = useSearchParams();
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

  const [installing, setInstalling] = React.useState<ConnectorKind | null>(null);
  const [scopeFor, setScopeFor] = React.useState<Connector | null>(null);
  const [disconnectFor, setDisconnectFor] = React.useState<Connector | null>(null);
  const [credentialsFor, setCredentialsFor] = React.useState<ConnectorKind | null>(null);

  // On mount: read ?connected={kind} param set by OAuthCallbackHandler and fire
  // a success toast (Connect Flow step 5 — UI-SPEC Copywriting Contract).
  React.useEffect(() => {
    const connectedKind = searchParams.get("connected");
    if (connectedKind) {
      const label = KIND_LABEL[connectedKind as ConnectorKind] ?? connectedKind;
      toast.success(`${label} connected`);
    }
    // searchParams reference is stable per Next.js App Router — run once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // install / disconnect mutations — Wave C: replace setTimeout stub with real
  // OAuth redirect handling (UI-SPEC Connect Flow steps 1-2).
  const installMutation = useMutation({
    mutationFn: async (kind: ConnectorKind) => {
      setInstalling(kind);
      return installConnector(kind);
    },
    onSuccess: (data) => {
      // Wave C: if FastAPI returned {redirectUrl}, navigate to the provider OAuth
      // consent page (Connect Flow step 2). The browser navigates away; button
      // state is moot after this point.
      if ("redirectUrl" in data && typeof data.redirectUrl === "string") {
        window.location.assign(data.redirectUrl);
        return;
      }
      // Non-OAuth / direct-install path (backward-compat): data is a Connector.
      const c = data as Connector;
      toast.success(`${c.name} connected`);
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err, kind) =>
      toast.error(`Couldn't connect ${KIND_LABEL[kind]}`, {
        description: err instanceof Error ? err.message : undefined,
      }),
    onSettled: () => setInstalling(null),
  });

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
  const visibleKinds = ALL_KINDS.filter((k) => permittedKinds.has(k));
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
  const connected = resolved.filter((c) => isConnected(c));
  const available = resolved.filter((c) => !isConnected(c));
  const attentionCount = connected.filter((c) => c.health === "degraded").length;

  // Connected connectors split by the level they were onboarded at, so the
  // cascade is visible rather than implied: an org-wide connector is inherited
  // by every unit and cannot be disconnected from inside one.
  const orgConnected = connected.filter((c) => c.scope === "organization");
  const unitConnected = connected.filter((c) => c.scope === "business_unit");

  // Defense-in-depth gate: M7.2 connector:manage permission (UI-SPEC Permission Gating Contract).
  // Passed down to the action buttons so they can disable + tooltip when false.
  const canManage = hasPermission(session, "connector:manage");

  const actionHandlers = {
    canManage,
    onInstall: (c: Connector) => installMutation.mutate(c.kind),
    onInspect: (c: Connector) => setScopeFor(c),
    onDisconnect: (c: Connector) => setDisconnectFor(c),
    onAddCredentials: (c: Connector) => setCredentialsFor(c.kind),
    isInstalling: (c: Connector) => installMutation.isPending && installing === c.kind,
  };

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
        <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
          <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
          Govern
        </div>
        <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
          Integrations
        </h1>
        <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">
          {isOrgAdmin
            ? `OAuth- and key-based connectors. Permit a kind below and choose who gets it — globally, or only the ${BUSINESS_UNIT_LABEL.toLowerCase()}s you name. Credentials live in the tenant's secrets vault, never echoed back; revocation is a one-click action.`
            : `The integrations your Organization Admin permitted this ${BUSINESS_UNIT_LABEL.toLowerCase()}. Connect the ones your teams need — credentials live in the tenant's secrets vault, never echoed back.`}
        </p>
      </header>

      {/* Status anchor — the focal summary that breaks the uniform-grid monotony */}
      <StatusStrip
        connected={connected.length}
        attention={attentionCount}
        available={available.length}
      />

      {/* The policy itself, at the top — everything below is its consequence. */}
      {isOrgAdmin && <ConnectorGrantsCard kinds={ALL_KINDS} kindLabel={(k) => KIND_LABEL[k]} />}

      {!isOrgAdmin && visibleKinds.length === 0 && !grantsQ.isLoading && (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            Your Organization Admin hasn&apos;t permitted any connectors for this{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} yet. Ask them to grant the integrations your teams
            need — nothing can be connected until they do.
          </p>
        </div>
      )}

      {/* Available — quiet, dense, scannable grid */}
      {available.length > 0 && (
        <section aria-labelledby="available-heading" className="space-y-4">
          <SectionLabel
            id="available-heading"
            eyebrow="Catalog"
            title="Available to connect"
            blurb="Connect a provider to unlock the agents that depend on it."
          />
          <ul className="grid items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {available.map((c) => (
              <AvailableConnectorTile
                key={c.id}
                connector={c}
                busy={actionHandlers.isInstalling(c)}
                {...actionHandlers}
              />
            ))}
          </ul>
        </section>
      )}

      {/* Connected — featured control rows, one section per onboarding level */}
      {orgConnected.length > 0 && (
        <section aria-labelledby="connected-org-heading" className="space-y-4">
          <SectionLabel
            id="connected-org-heading"
            eyebrow="Active · Organization"
            title="Onboarded org-wide"
            blurb={`Managed by an Organization Admin and inherited by every ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
          />
          <ul className="space-y-3">
            {orgConnected.map((c) => (
              <FeaturedConnectorRow
                key={c.id}
                connector={c}
                busy={actionHandlers.isInstalling(c)}
                {...actionHandlers}
              />
            ))}
          </ul>
        </section>
      )}

      {unitConnected.length > 0 && (
        <section aria-labelledby="connected-unit-heading" className="space-y-4">
          <SectionLabel
            id="connected-unit-heading"
            eyebrow={`Active · ${BUSINESS_UNIT_LABEL}`}
            title={
              scopedUnits.length === 1
                ? `Onboarded in ${scopedUnits[0]!.name}`
                : `Onboarded in a ${BUSINESS_UNIT_LABEL.toLowerCase()}`
            }
            blurb={`Managed by the ${BUSINESS_UNIT_LABEL.toLowerCase()}'s own Admin. Not visible to any other ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
          />
          <ul className="space-y-3">
            {unitConnected.map((c) => (
              <FeaturedConnectorRow
                key={c.id}
                connector={c}
                busy={actionHandlers.isInstalling(c)}
                {...actionHandlers}
              />
            ))}
          </ul>
        </section>
      )}

      {/* MCP servers — user-registered, creator-scoped (distinct from tenant connectors) */}
      <section aria-labelledby="mcp-heading" className="space-y-4">
        <McpServersPanel />
      </section>

      {/* Scope inspector */}
      <ScopeInspector connector={scopeFor} onClose={() => setScopeFor(null)} />

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

// ───────── Status strip — control-panel summary anchor ─────────

function StatusStrip({
  connected,
  attention,
  available,
}: {
  connected: number;
  attention: number;
  available: number;
}) {
  const segments: {
    icon: LucideIcon;
    value: number;
    label: string;
    tone: string;
    dim?: boolean;
  }[] = [
    {
      icon: CheckCircle2,
      value: connected,
      label: "Connected",
      tone: "text-success bg-success/12",
    },
    {
      icon: ShieldAlert,
      value: attention,
      label: attention === 1 ? "Needs attention" : "Need attention",
      tone: "text-warning bg-warning/15",
      dim: attention === 0,
    },
    {
      icon: CircleDashed,
      value: available,
      label: "Available",
      tone: "text-info bg-info/12",
    },
  ];
  return (
    <div
      className="border-line-soft bg-panel-elevated grid grid-cols-3 overflow-hidden rounded-2xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-12px_oklch(0_0_0_/_0.4)]"
      style={{
        animationName: "rise",
        animationDuration: "0.6s",
        animationDelay: "0.05s",
        animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
        animationFillMode: "both",
      }}
    >
      {segments.map((s, i) => (
        <div
          key={s.label}
          className={cn(
            "flex items-center gap-3.5 px-5 py-4",
            i > 0 && "border-line-soft border-l",
            s.dim && "opacity-55",
          )}
        >
          <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl", s.tone)}>
            <s.icon className="size-4.5" aria-hidden />
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="font-display text-2xl leading-none font-bold tabular-nums">
              {s.value}
            </span>
            <span className="text-muted-foreground mt-1 text-[12px]">{s.label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ───────── Shared permission-gated action buttons ─────────

type ActionProps = {
  connector: Connector;
  busy?: boolean;
  canManage?: boolean;
  onInstall: (c: Connector) => void;
  onInspect: (c: Connector) => void;
  onDisconnect: (c: Connector) => void;
  onAddCredentials: (c: Connector) => void;
};

/** Renders exactly the right buttons for a connector's state + the caller's RBAC.
 * `block` makes the single primary CTA fill its container (used by the tile). */
function ConnectorActions({
  connector,
  busy,
  canManage,
  connected,
  block,
  onInstall,
  onInspect,
  onDisconnect,
  onAddCredentials,
}: ActionProps & { connected: boolean; block?: boolean }) {
  const isCredentialKind = CREDENTIAL_KINDS.includes(connector.kind);
  const label = KIND_LABEL[connector.kind];

  if (connected) {
    return (
      <>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onInspect(connector)}
          aria-label={`Inspect ${label} scopes`}
        >
          <Eye className="size-4" aria-hidden />
        </Button>
        {isCredentialKind && canManage && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onAddCredentials(connector)}
            className="border-line-soft"
            aria-label={`Update ${label} credentials`}
          >
            <KeyRound className="size-4" aria-hidden />
            Update key
          </Button>
        )}
        <RequireRole capability="connector:revoke">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onDisconnect(connector)}
            className="border-line-soft"
          >
            <Unplug className="size-4" aria-hidden />
            Disconnect
          </Button>
        </RequireRole>
      </>
    );
  }

  // Credential-based providers (ADO / Jira) — primary action opens the key dialog.
  if (isCredentialKind) {
    return canManage ? (
      <Button
        size="sm"
        onClick={() => onAddCredentials(connector)}
        aria-label={`Add ${label} credentials`}
        className={cn(PRIMARY_CTA, block && "w-full")}
      >
        <KeyRound className="size-4" aria-hidden />
        Add credentials
      </Button>
    ) : (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className={cn(block && "w-full")}>
              <Button
                size="sm"
                disabled
                aria-label={`Add ${label} credentials`}
                className={cn(block && "w-full")}
              >
                <KeyRound className="size-4" aria-hidden />
                Add credentials
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            You need the connector:manage permission to configure integrations.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // OAuth providers — Connect button (gated on connector:manage / legacy install).
  const connectBtn = (disabled?: boolean) => (
    <Button
      size="sm"
      onClick={disabled ? undefined : () => onInstall(connector)}
      disabled={disabled || busy}
      aria-busy={busy}
      aria-label={`Connect ${label}`}
      className={cn(PRIMARY_CTA, block && "w-full")}
    >
      {busy ? (
        <Loader2 className="size-4 animate-spin" aria-hidden />
      ) : (
        <Plug className="size-4" aria-hidden />
      )}
      {busy ? "Authenticating…" : "Connect"}
    </Button>
  );

  return canManage ? (
    connectBtn()
  ) : (
    <RequireRole
      capability="connector:install"
      fallback={
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className={cn(block && "w-full")}>
                <Button
                  size="sm"
                  disabled
                  aria-label={`Connect ${label}`}
                  className={cn(block && "w-full")}
                >
                  <Plug className="size-4" aria-hidden />
                  Connect
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              You need the connector:manage permission to connect integrations.
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      }
    >
      {connectBtn()}
    </RequireRole>
  );
}

// ───────── Featured connected row ─────────

function FeaturedConnectorRow({ connector, busy, ...handlers }: ActionProps) {
  const isPendingValidation =
    connector.health === "degraded" && VALIDATION_PENDING_KINDS.includes(connector.kind);
  return (
    <li>
      <Card className="border-brand-bright/25 group bg-panel-elevated relative flex flex-col gap-4 overflow-hidden rounded-2xl border p-5 shadow-[0_1px_0_oklch(1_0_0_/_0.05)_inset,0_10px_30px_-14px_oklch(0_0_0_/_0.5)] ring-1 ring-brand-bright/10 transition-shadow hover:shadow-[0_16px_40px_-16px_oklch(0_0_0_/_0.55)] sm:flex-row sm:items-center">
        {/* Soft brand wash so the connected row reads as the page's focal element */}
        <span
          aria-hidden
          className="from-brand-bright/[0.06] pointer-events-none absolute inset-0 bg-gradient-to-r to-transparent"
        />
        <div className="relative flex min-w-0 flex-1 items-center gap-4">
          <KindGlyph kind={connector.kind} size={12} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="font-display truncate text-[16px] font-bold tracking-[-0.01em]">
                {KIND_LABEL[connector.kind]}
              </h3>
              <HealthPill connector={connector} />
            </div>
            <p className="text-muted-foreground mt-0.5 truncate text-[12.5px]">
              {KIND_TAGLINE[connector.kind]}
            </p>
            <div className="text-muted-foreground mt-2 flex flex-wrap items-center gap-x-4 gap-y-0.5 font-mono text-[11px]">
              {connector.account && <span className="truncate">{connector.account}</span>}
              {connector.lastCheckedAt && (
                <span>
                  Synced{" "}
                  {formatDistanceToNow(new Date(connector.lastCheckedAt), { addSuffix: true })}
                </span>
              )}
            </div>
            {isPendingValidation && (
              <p className="text-warning mt-1.5 font-mono text-[11px]">
                Live validation pending — OAuth app registration required
              </p>
            )}
            {/* Drill-in, matching the provider cards on Models: the card says
                whether it is connected and healthy, the detail says which
                business units may use it and where its credentials live. */}
            <Link
              href={`/integrations/${encodeURIComponent(connector.kind)}`}
              className="text-brand-bright mt-2 inline-flex items-center gap-1 font-mono text-[11px] underline-offset-2 hover:underline"
            >
              Connections &amp; access
              <ArrowRight className="size-3" aria-hidden />
            </Link>
          </div>
        </div>
        <div className="relative flex shrink-0 flex-wrap items-center gap-1.5 sm:justify-end">
          <ConnectorActions connector={connector} busy={busy} connected {...handlers} />
        </div>
      </Card>
    </li>
  );
}

// ───────── Available connector tile ─────────

function AvailableConnectorTile({ connector, busy, ...handlers }: ActionProps) {
  const wasInstalled = connector.installed; // disconnected but still installed → show status
  return (
    <li className="h-full">
      <Card className="border-line-soft bg-panel-elevated/70 hover:border-line-soft/0 flex h-full flex-col gap-3 rounded-2xl border p-4 shadow-[0_1px_0_oklch(1_0_0_/_0.03)_inset] transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-[0_14px_30px_-16px_oklch(0_0_0_/_0.5)] hover:ring-1 hover:ring-brand-bright/20">
        <div className="flex items-start justify-between gap-2">
          <KindGlyph kind={connector.kind} />
          <span className="text-muted-foreground/80 bg-muted/40 rounded-full px-2 py-0.5 font-mono text-[9.5px] tracking-[0.08em] uppercase">
            {chipLabel(connector.kind)}
          </span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-display truncate text-[14.5px] font-bold tracking-[-0.01em]">
              {KIND_LABEL[connector.kind]}
            </h3>
            {wasInstalled && <HealthPill connector={connector} compact />}
          </div>
          <p className="text-muted-foreground mt-1 line-clamp-2 text-[12px] leading-snug">
            {KIND_TAGLINE[connector.kind]}
          </p>
        </div>
        <div className="mt-1">
          <ConnectorActions
            connector={connector}
            busy={busy}
            connected={false}
            block
            {...handlers}
          />
        </div>
      </Card>
    </li>
  );
}

// ───────── Health pill — ConnectorHealth tones via tokens ─────────

function HealthPill({ connector, compact }: { connector: Connector; compact?: boolean }) {
  if (!connector.installed) return null;
  const tone =
    connector.health === "healthy"
      ? "text-success bg-success/10 border-success/30"
      : connector.health === "degraded"
        ? "text-warning bg-warning/15 border-warning/40"
        : "text-destructive bg-destructive/10 border-destructive/30";
  const Icon: LucideIcon =
    connector.health === "healthy"
      ? CheckCircle2
      : connector.health === "degraded"
        ? ShieldAlert
        : Unplug;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border font-mono font-semibold capitalize",
        compact ? "px-1.5 py-0 text-[9px]" : "px-2 py-0.5 text-[10px]",
        tone,
      )}
    >
      <Icon className={compact ? "size-2.5" : "size-3"} aria-hidden />
      {connector.health}
    </span>
  );
}

// ───────── Scope inspector ─────────

function ScopeInspector({
  connector,
  onClose,
}: {
  connector: Connector | null;
  onClose: () => void;
}) {
  const open = !!connector;
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">
            Scope · <span className="font-mono text-sm font-normal">{connector?.name}</span>
          </DialogTitle>
          <DialogDescription>
            Exactly what this token is allowed to do. Requested at install time.
          </DialogDescription>
        </DialogHeader>
        {connector && (
          <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
            {connector.capabilities.length === 0 ? (
              <li className="text-muted-foreground p-3 text-sm">No capabilities declared.</li>
            ) : (
              connector.capabilities.map((cap: Capability) => (
                <li key={cap.key} className="flex items-center gap-3 p-3 text-sm">
                  <Badge
                    variant="outline"
                    className={cn(
                      "w-14 justify-center font-mono text-[10px] uppercase",
                      cap.mode === "read" && "text-info",
                      cap.mode === "write" && "text-warning",
                      cap.mode === "event" && "text-muted-foreground",
                    )}
                  >
                    {cap.mode}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-xs">{cap.key}</div>
                    <div className="text-muted-foreground text-xs">{cap.description}</div>
                  </div>
                </li>
              ))
            )}
          </ul>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="border-line-soft">
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
  const isGithubActions = kind === "github_actions";
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
      setUnitId("");
      setPending(false);
    }
  }, [open]);

  const fieldsValid = isJira
    ? Boolean(baseUrl.trim() && email.trim() && apiToken.trim())
    : isGithubActions
      ? Boolean(ghToken.trim())
      : Boolean(orgUrl.trim() && pat.trim());
  const canSubmit = fieldsValid && (!mustChooseUnit || Boolean(targetUnitId));

  const handleSubmit = async () => {
    if (!kind || !canSubmit || pending) return;
    setPending(true);
    try {
      const fields = isJira
        ? { base_url: baseUrl.trim(), email: email.trim(), api_token: apiToken }
        : isGithubActions
          ? { pat: ghToken, owner: ghOwner.trim() || undefined }
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
              : isGithubActions
                ? "Paste a GitHub Personal Access Token (scopes: repo, workflow) and, optionally, the owner/org. Stored in the tenant's secrets vault, verified with a live probe, and never shown again."
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

          {isGithubActions ? (
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
          ) : isJira ? (
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
            </>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="ado-org-url">Organization URL</Label>
                <Input
                  id="ado-org-url"
                  value={orgUrl}
                  onChange={(e) => setOrgUrl(e.target.value)}
                  placeholder="https://dev.azure.com/your-org"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ado-pat">Personal Access Token</Label>
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
