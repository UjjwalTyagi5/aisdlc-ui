import type { ProjectId, RunId, ArtifactId } from "@/lib/schemas";

/**
 * Central query-key factory. Prevents key drift and makes bulk
 * invalidation trivial:
 *
 *   queryClient.invalidateQueries({ queryKey: qk.projects.all() });
 *   queryClient.invalidateQueries({ queryKey: qk.runs.forProject(id) });
 */
export const qk = {
  session: {
    me: () => ["session", "me"] as const,
    /** The viewer's resolved Business Unit / project scope. */
    accessScope: () => ["session", "access-scope"] as const,
  },
  workspaces: {
    all: () => ["workspaces"] as const,
    list: () => ["workspaces", "list"] as const,
    detail: (id: string) => ["workspaces", "detail", id] as const,
    members: (id: string) => ["workspaces", "members", id] as const,
  },
  projects: {
    all: () => ["projects"] as const,
    list: (filters?: Record<string, unknown>) =>
      ["projects", "list", filters ?? {}] as const,
    detail: (id: ProjectId) => ["projects", "detail", id] as const,
    /** The integrations approved for a project, with its own credentials. */
    integrations: (id: string) => ["projects", "integrations", id] as const,
  },
  projectMembers: {
    list: (id: ProjectId) => ["project-members", "list", id] as const,
  },
  users: {
    /** The org-wide people directory (Users & Roles). */
    directory: () => ["users", "directory"] as const,
    detail: (id: string) => ["users", "detail", id] as const,
  },
  governanceApprovals: {
    list: (workspaceId?: string) => ["governance-approvals", "list", workspaceId ?? ""] as const,
  },
  runs: {
    all: () => ["runs"] as const,
    list: (filters?: Record<string, unknown>) =>
      ["runs", "list", filters ?? {}] as const,
    forProject: (id: ProjectId) => ["runs", "project", id] as const,
    detail: (id: RunId) => ["runs", "detail", id] as const,
    steps: (id: RunId) => ["runs", "detail", id, "steps"] as const,
    audit: (id: RunId) => ["runs", "detail", id, "audit"] as const,
    eval: (id: RunId) => ["runs", "detail", id, "eval"] as const,
  },
  artifacts: {
    all: () => ["artifacts"] as const,
    /** A project's artifacts, optionally narrowed to one phase.
     *
     * THE PHASE IS PART OF THE KEY because it is part of the REQUEST. Four call sites
     * fetched `listArtifacts(projectId, { phase })` with four different phases and
     * cached them all under one key: whichever loaded first won, so opening Plan after
     * Requirements showed Requirements' documents and stories under a Plan heading.
     *
     * Omitting `phase` still yields the bare `["artifacts", "project", id]`, which is a
     * PREFIX of every phased key — so the existing
     * `invalidateQueries({ queryKey: forProject(id) })` calls keep clearing every
     * phase's cache, which is what they were always meant to do. */
    forProject: (id: ProjectId, phase?: string) =>
      (phase
        ? ["artifacts", "project", id, phase]
        : ["artifacts", "project", id]) as readonly unknown[],
    detail: (id: ArtifactId) => ["artifacts", "detail", id] as const,
  },
  // Frozen stage payload versions and their publication state. Deliberately a
  // separate namespace from `artifacts` above: that one is blob documents, this one
  // is the JSONB hand-off between agents, and they have independent lifecycles.
  artifactVersions: {
    all: () => ["artifact-versions"] as const,
    forStage: (id: ProjectId, phase: string) =>
      ["artifact-versions", id, phase] as const,
    published: (id: ProjectId, phase: string) =>
      ["artifact-versions", id, phase, "published"] as const,
    matrix: (id: ProjectId) => ["artifact-versions", id, "matrix"] as const,
    runEvidence: (id: ProjectId, runId: string) =>
      ["artifact-versions", id, "run", runId] as const,
    versionConsumers: (id: ProjectId, phase: string, version: number) =>
      ["artifact-versions", id, phase, version, "consumers"] as const,
  },
  connectors: {
    list: (workspaceId?: string | null) => ["connectors", workspaceId ?? ""] as const,
    detail: (kind: string) => ["connectors", kind] as const,
    grants: (workspaceId?: string | null) => ["connectors", "grants", workspaceId ?? ""] as const,
  },
  /** The whole estate crossed with who holds it — the Integrations matrix. */
  integrationAccess: {
    list: () => ["integration-access", "list"] as const,
  },
  model: {
    catalog: () => ["model", "catalog"] as const,
    providers: (workspaceId?: string | null) => ["model", "providers", workspaceId ?? ""] as const,
    /** Scoped by project: two projects in different units have different option sets. */
    options: (projectId?: string | null) => ["model", "options", projectId ?? ""] as const,
    orgGrants: () => ["model", "allowed", "org"] as const,
    buAllowed: (workspaceId: string) => ["model", "allowed", "bu", workspaceId] as const,
    availability: (workspaceId: string) => ["model", "availability", workspaceId] as const,
    grantedProviders: (workspaceId: string) =>
      ["model", "granted-providers", workspaceId] as const,
    /** Org Admin only: every model crossed with every business unit. */
    grantMatrix: () => ["model", "grant-matrix"] as const,
    /** Org Admin only: which business units may use which provider. */
    providerGrants: () => ["model", "providerGrants"] as const,
  },
  mcp: {
    list: (activeOnly?: boolean, workspaceId?: string | null) =>
      ["mcp", "list", activeOnly ?? false, workspaceId ?? null] as const,
    detail: (id: string) => ["mcp", "detail", id] as const,
  },
  conversations: {
    list: (projectId: ProjectId, agentId: string) =>
      ["conversations", projectId, agentId] as const,
    messages: (id: string) => ["conversations", "messages", id] as const,
  },
  capabilities: {
    forProject: (id: ProjectId) => ["capabilities", "project", id] as const,
  },
  agentAccessOverrides: {
    forProject: (id: ProjectId) => ["agent-access-overrides", id] as const,
  },
  agentProfiles: {
    summary: (scope: string, scopeId?: string | null) =>
      ["agent-profiles", "summary", scope, scopeId ?? ""] as const,
    versions: (agentId: string, scope: string, scopeId?: string | null) =>
      ["agent-profiles", "versions", agentId, scope, scopeId ?? ""] as const,
  },
  agentSkills: {
    list: (agentId: string, scope: string, scopeId?: string | null) =>
      ["agent-skills", "list", agentId, scope, scopeId ?? ""] as const,
    detail: (
      origin: string,
      skillKey: string,
      agentId: string,
      scope: string,
      scopeId?: string | null,
    ) =>
      ["agent-skills", "detail", origin, skillKey, agentId, scope, scopeId ?? ""] as const,
    versions: (
      skillKey: string,
      agentId: string,
      scope: string,
      scopeId?: string | null,
    ) => ["agent-skills", "versions", skillKey, agentId, scope, scopeId ?? ""] as const,
  },
  codeReview: {
    prs: (id: ProjectId, p: string, r: string) =>
      ["code-review", "prs", id, p, r] as const,
    reviews: (id: ProjectId) => ["code-review", "reviews", id] as const,
    review: (id: ProjectId, runId: string) =>
      ["code-review", "review", id, runId] as const,
  },
  security: {
    prs: (id: ProjectId, p: string, r: string) =>
      ["security", "prs", id, p, r] as const,
    scans: (id: ProjectId) => ["security", "scans", id] as const,
    scan: (id: ProjectId, runId: string) =>
      ["security", "scan", id, runId] as const,
  },
  deployment: {
    connectors: (id: ProjectId) => ["deployment", "connectors", id] as const,
    prs: (id: ProjectId, p: string, r: string, provider?: string) =>
      ["deployment", "prs", id, p, r, provider ?? ""] as const,
    release: (id: ProjectId, session: string) =>
      ["deployment", "release", id, session] as const,
  },
  testing: {
    unitResult: (id: ProjectId, session: string) =>
      ["testing", "unit-result", id, session] as const,
  },
  documentation: {
    connectors: (id: ProjectId) => ["documentation", "connectors", id] as const,
    prs: (id: ProjectId, p: string, r: string, provider?: string) =>
      ["documentation", "prs", id, p, r, provider ?? ""] as const,
    docset: (id: ProjectId, session: string) =>
      ["documentation", "docset", id, session] as const,
  },
  audit: {
    list: (filters?: Record<string, unknown>) =>
      ["audit", filters ?? {}] as const,
  },
  cost: {
    breakdown: (windowDays?: number, workspace?: string | null) =>
      ["cost", "breakdown", windowDays ?? 30, workspace ?? "all"] as const,
    budgets: () => ["cost", "budgets"] as const,
    spendSeries: (
      groupBy: string,
      workspaceId: string | null,
      months: number,
      projectId?: string | null,
    ) =>
      ["cost", "spend-series", groupBy, workspaceId ?? "all", months, projectId ?? "all"] as const,
  },
  org: {
    overview: () => ["org", "overview"] as const,
  },
  traces: {
    all: () => ["traces"] as const,
    list: (filters?: unknown) => ["traces", "list", filters ?? {}] as const,
    metrics: (windowDays?: number, filters?: unknown) =>
      ["traces", "metrics", windowDays ?? 30, filters ?? {}] as const,
    detail: (id: string) => ["traces", "detail", id] as const,
    projectSummary: (projectId: string, windowDays?: number) =>
      ["traces", "project-summary", projectId, windowDays ?? 7] as const,
  },
  approvals: {
    all: () => ["approvals"] as const,
    list: (filters?: unknown) => ["approvals", "list", filters ?? {}] as const,
    metrics: () => ["approvals", "metrics"] as const,
  },
  notifications: {
    list: () => ["notifications"] as const,
  },
  access: {
    workspaces: () => ["access", "workspaces"] as const,
    roles: () => ["access", "roles"] as const,
    members: (workspaceId: string) => ["access", "members", workspaceId] as const,
    /** The whole org roster, with no workspace context — see listOrgMembers. */
    orgMembers: () => ["access", "org-members"] as const,
  },
  devWorkspace: {
    // THE PROVIDER IS PART OF THE KEY. Without it, switching host in a dialog would
    // show the previous one's cached repositories under the new one's name — the list
    // would look right and belong to somewhere else.
    sources: (id: ProjectId) => ["dev-workspace", "sources", id] as const,
    adoProjects: (id: ProjectId, provider?: string) =>
      ["dev-workspace", "ado-projects", id, provider ?? ""] as const,
    adoRepos: (id: ProjectId, p: string, provider?: string) =>
      ["dev-workspace", "ado-repos", id, p, provider ?? ""] as const,
    adoBranches: (id: ProjectId, p: string, r: string, provider?: string) =>
      ["dev-workspace", "ado-branches", id, p, r, provider ?? ""] as const,
    workspace: (id: ProjectId) => ["dev-workspace", "workspace", id] as const,
    tree: (id: ProjectId) => ["dev-workspace", "tree", id] as const,
    file: (id: ProjectId, path: string) => ["dev-workspace", "file", id, path] as const,
    changes: (id: ProjectId) => ["dev-workspace", "changes", id] as const,
    changedLines: (id: ProjectId, path: string) =>
      ["dev-workspace", "changed-lines", id, path] as const,
    prs: (id: ProjectId) => ["dev-workspace", "prs", id] as const,
  },
} as const;
