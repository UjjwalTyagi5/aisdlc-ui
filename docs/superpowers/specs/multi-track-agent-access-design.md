# Multi-Track Agent Access — Research & Design

Date: 2026-08-19
Owner: Sarthak
Scope: research/brainstorming pass answering how a project's **track** should decide
which AI agents exist inside it, and how a person's **role** should decide which of
those agents they can actually open and act on — on screen, and inside the backend that
actually runs them. Source material: `Prd (1).md` §§14–15, 20–25, 34–41, and a full
survey of the current frontend and backend code.

**Status: research complete, nothing implemented.** This document is the deliverable of
that research — it is meant to be read by the whole team, technical and non-technical
alike, before any of it becomes an implementation plan. Part 1 assumes no code
knowledge. Parts 4 onward assume it.

**A note on "already exists."** This document names several places in the codebase
where something with the right shape or the right name already has files written for
it — a registry, 8 agent routers, an Orchestrator page. Treat every one of those as a
**non-functional first pass, not a working feature to extend.** None of it has been
properly built against the rules in this document or tested end-to-end, and it should
be rebuilt and verified properly as part of this work, not assumed to already do what
its name suggests. Where this document says something "exists," read that as "there are
files here to look at and largely start over from," not "there is a working feature
here."

---

## Part 1 — What this is, in plain language

### 1.1 The problem, in one sentence

Every project today shows the same fixed set of 8 agent tiles to everyone, and nothing
on the server actually stops a person from invoking an agent that isn't theirs to run —
the current "locked" tiles are a visual hint in the UI, not a real restriction. We need
two things working together: a project's **track** decides *which* agents exist inside
it at all, and a person's **role** on that project decides which of those agents they
can actually open and act on — enforced for real, not just hidden.

### 1.2 Background: five tracks, not one

The platform used to have a single delivery pipeline (Requirements → Design →
Development → Code Review → Security → Testing → Deployment → Documentation). There is
old code in the repository for these 8 agents, but it should be treated as a rough,
unverified first pass, not something to build on top of — it needs to be properly
rebuilt and tested, not patched. The PRD has since split delivery into **five tracks**,
chosen once per project, each shaped for a different kind of work:

| # | Track | What kind of work it's for |
|---|---|---|
| 1 | **Greenfield Implementation** | A blank-slate build — nothing exists yet |
| 2 | **Enhancement & Support** | Changing something that already exists (a bug fix, a feature add, an incident fix) |
| 3 | **Code Modernization** | Migrating/upgrading an existing codebase to a new language, framework, or version |
| 4 | **RPA & Infrastructure Migration** | Moving an automation bot to a new platform, replacing a bot with real code, or migrating infrastructure (servers, cloud, databases) |
| 5 | **Data Engineering** | Building or changing a data pipeline (source → warehouse/lake → reporting) |

Each track is "a configurable template, not a separate product" (PRD §2) — same
platform, same roles, same governance model, same approval mechanics throughout. What
changes per track is only *which agents run, and in what order*.

### 1.3 The five agent rosters

| Track | Agents, in hand-off order | Count | Status |
|---|---|---|---|
| **1 — Greenfield** | Requirements → Design → Development → Code Review → Security → Testing → Deployment → Documentation | 8 | Old, unverified code exists for all 8 — **needs to be properly rebuilt and tested**, not extended as-is |
| **2 — Enhancement & Support** | Same 8 agents, same order — Design is skipped automatically if the change doesn't actually need one | 8 | Same code as Track 1 — same rebuild-and-test requirement |
| **3 — Code Modernization** | Requirements → **Discovery & Assessment** → Design → **Strategy** → Development → Code Review → Security → Testing → Deployment → Documentation | 10 | Nothing exists — PRD marks this entire track "target design" |
| **4 — RPA & Infra Migration** | Requirements → **Discovery & Assessment** → **Migration Mapping** → Development → Security → **Validation** → Deployment → Documentation | 8 | Nothing exists — target design |
| **5 — Data Engineering** | Requirements → **Data Engineering** → Security → Testing → Deployment → Documentation | 6 | Nothing exists — target design |

Bold names are agents no other track uses. Notice Track 5 has no Design, Development, or
Code Review stage — there's no application code being built, just a data pipeline, so
those stages simply don't apply.

To be clear about Track 1/2's status: "old code exists" is not the same claim as "these
agents work." Nobody has verified any of the 8 against the rules in this document —
proper agent access, real approval gates, a real Orchestrator hand-off. Building them
properly, with real tests, is itself part of the work ahead, not a step that's already
done.

### 1.4 Each track owns its own agents — this is 4 portfolios, not 1 shared pool

It's tempting to think of this as one shared pool of about a dozen agent *types*, with
different tracks just borrowing the ones they need — a single "Security" agent, a
single "Requirements" agent, reused everywhere. **That is not how this should be built,
and it's not how the PRD treats it either.** Only Track 2
(Enhancement & Support) genuinely reuses another track's agents — it runs Track 1's
exact 8 agents, unchanged. Every other track defines and builds its **own**, independent
set of agents, even in cases where one of them happens to share a name and an owning
role with an agent in a different track:

| Portfolio | Tracks it belongs to | Agent count | Shared with another track? |
|---|---|---|---|
| **Greenfield/Brownfield portfolio** | Track 1, Track 2 | 8 | Yes — Track 2 reuses Track 1's 8 agents exactly |
| **Modernization portfolio** | Track 3 | 10 | No — entirely its own |
| **RPA & Infra Migration portfolio** | Track 4 | 8 | No — entirely its own |
| **Data Engineering portfolio** | Track 5 | 6 | No — entirely its own |

That's **4 independent portfolios, 32 agent definitions in total** — 8 of which are
shared between Track 1 and Track 2 specifically, and 24 that exist in exactly one track
and nowhere else. A Track 4 project's "Security Agent" is not a pointer to Track 1's
Security Agent with a different mode flag; it is its own agent, built, configured, and
registered under Track 4, even though — see below — its owning role happens to match.

**One pattern worth knowing, precisely because it's a pattern and not a mechanism:**
whenever an agent's *name* recurs across portfolios (Security, Testing/Validation,
Deployment, Documentation, and Requirements all show up in more than one), its *owning
role* is always the same person doing it — the Security Agent is always the Security
Engineer's, in every portfolio it appears in. That consistency is a deliberate design
convention worth preserving when new portfolios get built (see Part 5), not evidence
that the agents are secretly the same underlying object. The full agent list for each
portfolio, with its own owner, is in Part 3.

Two roles never touch an agent at all, in any portfolio — **Organization Admin and
Business Unit Admin are governance-only.** They can grant *other* people access to an
agent, but never chat with one themselves (PRD §14.8 — a hard rule, not an oversight).

**Project Admin is the fallback owner of every agent on every track, always.** If the
role that actually owns an agent is unavailable, the Project Admin can step in and
approve on their behalf. Project Admin is the one role with some form of access to the
entire roster, on any track, without needing an explicit grant.

The full role × agent involvement chart for the 8 core agents, exactly as the PRD
specifies it (§14.7), is reproduced in the Appendix.

### 1.5 What "owning" an agent actually means, day to day

Every agent's work is split into three tiers, and this decides who has to click
"approve" on what:

- **Safe** — the agent just does it. Drafting a document, generating code in a sandbox,
  running a scan, producing a diagram. No approval needed from anyone, from any role.
- **Consequential** — a real action that reaches outside the platform: writing to Jira,
  pushing a branch, opening a PR, triggering a pipeline. The **owning role** approves it
  before it happens.
- **Sign-off** — a formal, audited acceptance that a whole stage of work is genuinely
  done (accepting a design, baselining requirements, issuing the mandatory security
  verdict). Also the owning role's call — and the one place self-approval is never
  allowed anywhere on the platform: whoever ran the agent is never the one who accepts
  its own output.

So "owning" an agent means: you're the person the platform asks to approve its
Consequential writes and its Sign-offs. Everyone else who has *some* access to that
agent can still open it, chat with it, and use its Safe capabilities — they just can't
approve anything on it.

One PRD rule of thumb worth knowing (§20.3): if an agent's write *ships or releases*
something to the outside world, the Sign-off comes first, then the write (e.g.
Deployment: approve the release, *then* deploy). If the write only *puts work in front
of other people* to review, the write happens first and the Sign-off follows (e.g.
Development: push the branch so Code Review can see it, *then* accept the
implementation).

### 1.6 The actual user journey

1. **A Business Unit Admin (or a Project-Admin-capable user submitting a request) creates
   a project** and, as one of the very first questions, picks its track. This can't be
   changed afterward — picking the wrong track means starting a new project, not editing
   this one.
2. **The project is staffed** — a BA, an Architect, a Developer, and so on are added,
   each in their ordinary role, exactly as today.
3. **Everyone who opens the project sees the same fixed set of pages**, but the row of
   agent tiles is specific to that project's track: a Track 5 project shows 6 tiles, a
   Track 1 project shows 8. Within that row:
   - **Their own owned agent** is fully open — chat, Safe capabilities, and they're the
     one asked to approve its gates.
   - **Every other agent in the roster** is still open to chat with and use Safe
     capabilities on, just not to approve anything on (the PRD's "use other agents").
   - **Any agent not yet properly built and tested** — everything in Tracks 3, 4, 5
     today, since nothing exists for them yet, and every Portfolio 1/2 agent until its
     rebuild is actually done and verified — renders as "Coming soon" and isn't
     clickable. The roster slot is real; whether a working agent sits behind it is a
     separate, earned state, not something old code or a track selection grants by
     itself.
4. **Work happens by chatting with an agent directly**, or through the **Orchestrator** —
   a cross-agent cockpit. Only a Project Admin can actually *drive* it (move work from
   one stage to the next); everyone else can open it to see where things stand, but the
   "advance to the next agent" action is Project-Admin-only (PRD §15.4, §34.11: "a
   conversation partner, not an automatic sequencer — the person decides every move").
5. **If someone genuinely needs extra access** — a Business Unit Admin or Project Admin
   can grant that, per project, per agent, and it's revocable the same way, independent
   of the default ownership chart in §1.4 (PRD §14.8: "an admin can grant a person extra
   agent access... independent of that person's default ownership/contribution"). The
   grant can be made at **either of two grains**, both supported from the start:
   - **Role-wide, on one project** — e.g. "on this project, every Developer may also use
     the Security Agent." Reaches everyone currently holding that role on the project,
     automatically including anyone added to it later.
   - **One named person, on one project** — e.g. "Farah specifically may also use the
     Security Agent on this project," without opening it to every Data Engineer on the
     team. Useful when the need is genuinely individual, not role-wide.

   The two behave identically once granted (chat + Safe capabilities on that agent,
   still not approval rights, which stay with the owning role) — they differ only in who
   the grant reaches, and both are revoked the same way a role/role-binding grant is
   revoked elsewhere in the platform.

### 1.7 A worked example

*Priya (Business Unit Admin) creates a new project, "Warehouse Migration," and picks
Track 5 — Data Engineering, since the work is standing up a new reporting pipeline. She
staffs it: Amit as BA, Farah as Data Engineer, Raj as Security Engineer, Lina as
QA/Tester, Tom as DevOps Engineer.*

*When any of them opens the project, they see six tiles: Requirements, Data Engineering,
Security, Testing, Deployment, Documentation — no Design, Development, or Code Review
tiles at all, because Track 5's roster doesn't include them.*

*Amit opens Requirements (his own) and drafts the data-intent brief — what source system,
what target warehouse, the data-quality bar expected. Farah can also open Requirements to
read it (Use access), but can't approve anything on it. Once Amit baselines it
(Sign-off), Farah opens the Data Engineering tile — hers to own — and the agent picks up
the brief automatically, generates the pipeline code and connector config, and produces
a cost/performance report. Raj, Lina, and Tom can all open the Data Engineering tile to
read what it's produced, but only Farah can approve its Consequential writes (registering
the connector, deploying the pipeline) and its final Sign-off.*

*Later, Farah asks Priya whether she can also see the Security Agent directly on this
project, to sanity-check a PII flag before Raj gets to it. Since it's specifically Farah
who needs this, not the Data Engineer role in general, Priya grants it as a
person-level override — just "Farah, on this project, may also use Security." If a
second Data Engineer joined the project later needing the same thing, Priya could
instead have granted it role-wide ("every Data Engineer on this project") so it applies
automatically without a second one-off grant. Either way, Raj still owns Security's
Consequential writes and its mandatory Sign-off — the override only ever reaches
chat/Safe access.*

### 1.8 What this document does not change

This does not touch: who can grant or revoke *roles* (that RBAC work already shipped
this session), the approval/sign-off mechanics themselves, or what each agent actually
produces. It only decides which agent tiles a person sees and can act on, and which
agents exist in a project at all, based on its track.

### 1.9 Glossary

| Term | Meaning |
|---|---|
| Track | The delivery template a project is built on (one of 5); chosen once, at creation |
| Agent | A single AI-driven stage in the pipeline (e.g. Requirements, Security) |
| Roster | The ordered list of agents a given track includes |
| Owner | The role that approves an agent's Consequential writes and Sign-offs |
| Use access | Can open/chat with an agent and run its Safe capabilities, but not approve anything |
| Fallback owner | Project Admin — can approve on any agent's behalf if the real owner is unavailable |
| Override | An admin-granted exception giving a role extra agent access on one project, beyond its default |
| Safe / Consequential / Sign-off | The three approval tiers an agent's actions fall into — see §1.5 |
| Orchestrator | The cross-agent cockpit that moves a workstream from one agent to the next |

---

## Part 2 — Frontend flow (screens and states)

### 2.1 Project creation — picking a track

The project-creation flow (PRD §38.1, "Request a project") already captures "delivery
track" as one of the fields collected up front, alongside name, business intent, scope,
and budget. Concretely this becomes a track-picker step: five cards, one per track, each
with a one-line description and the agent roster it implies (reusing the table in §1.3
as the on-screen copy), so the person choosing understands the consequence of their pick
before committing. Once submitted — and, per the existing flow, approved by a Business
Unit Admin — the track is locked to the project record permanently.

### 2.2 Inside a project — the agent grid

There is an existing agent-tile grid in the codebase, but — per the note at the top of
this document — it should be treated as an unverified first pass and rebuilt properly
against the rules below, reusing only whatever of its current visual language still
fits, not assumed to already behave correctly:

| Tile state | When it applies | What the person sees |
|---|---|---|
| **Full access (owner)** | This is the person's owned agent, and that agent has been properly built and tested | Fully open; approve controls appear inline when a gate fires |
| **Use-only** | Agent is in this project's roster, properly built and tested, and the person's role owns or contributes to it | Fully open to chat/Safe work; no approve controls |
| **Locked — request access** | Agent is in the roster, properly built and tested, but the person's role has no access and no override exists | "Request access" action, routes to the owning role or an admin |
| **Coming soon** | Agent is in the track's roster but hasn't been properly built and verified yet — whether or not old code for it happens to exist | Non-interactive, badge only — distinct from "locked," since no amount of access-granting makes an unverified agent usable |

Only agents in the *project's own track roster* render at all — a Track 5 project never
shows a Development tile, locked or otherwise, because Development isn't part of that
track's roster in the first place. This is a change from today, where all agents render
for every project regardless of track and only the lock state varies.

### 2.3 The Orchestrator cockpit

There is a page in the codebase at
`frontend/app/(app)/projects/[id]/orchestrator/page.tsx` with "Orchestrator" in its
name, and it currently drives through a project's agent roster in hand-off order — but,
per the note at the top of this document, it should be treated as an unproven
placeholder, not a working cross-agent cockpit to extend. It has never been built or
tested against the rules the PRD actually specifies for it (§15.4, §34.11), including
the two below. Building a real Orchestrator means implementing and verifying, from
scratch if needed, at least:

- Its roster is driven by the project's track, exactly like the tile grid — never a
  fixed list of 8 regardless of track.
- The "advance to next stage"/"drive" controls are visible only to the Project Admin,
  per §1.6, point 4. Everyone else who opens the cockpit sees the same read-only view of
  where the workstream stands (current stage, pending approvals) already available from
  the Project Dashboard.
- Any individual chat turn a person initiates *from inside* the cockpit against a
  specific agent passes through the same per-agent access check as opening that agent
  directly. Driving the cockpit grants the stage-advance action, not blanket access to
  every agent in it.
- The cockpit only ever offers agents that have actually been properly built and
  verified (§2.2's "Coming soon" state applies here too) — it should never let someone
  advance a workstream into an agent that hasn't been through that process.

### 2.4 Roles & Access — granting extra agent access

The existing Roles & Access screen already has the interaction pattern for scope-confined
grants (project / BU / org). A per-agent override reuses that exact pattern, with one
extra choice up front — **grant to a role, or to one named person** (§1.6, point 5):
pick the project, pick "a role" or "a specific person," pick which one, tick which extra
agents (from that project's own portfolio only) they get, save — recorded in the same
audit trail as every other grant/revoke (PRD §14.12). No new page is needed; this is one
additional control on the existing grant flow.

### 2.5 Empty and blocked states

Following the PRD's own state model (§37), an agent tile or Orchestrator step needs a
defined behavior for each of these, not a blank screen:

| State | What it means here |
|---|---|
| Empty | A brand-new project on a track with no runs started yet — each tile shows its purpose and a "start" action, not a blank tile |
| Paused (gate) | A Consequential action or Sign-off fires — the tile shows what's pending and who it's waiting on |
| Paused (clarification) | The agent needs a decision before continuing — lands on whoever is running it, resumes exactly where it left off |
| Blocked (access denied) | A person without access attempts an action directly — clear reason shown, action refused, attempt recorded in the audit trail (§37 "deny-by-default; legible failure") |

---

## Part 3 — The complete agent portfolios, track by track

This section is the working reference for what each agent actually does, its owning
role, and its approval flow — condensed from the PRD's full agent-by-agent detail
(§§21–25) into one place, organized by portfolio (§1.4), for anyone (BA or engineer) who
needs to know what a specific agent is responsible for. Each of the four portfolios
below is independent — nothing in one is a reference to or reuse of another, except
Track 2, which is explicitly Track 1's own 8 agents run a second time.

### Portfolio 1 — Greenfield/Brownfield (Track 1 & Track 2, old code exists, not verified)

| # | Agent | Owner | What it does | Its gate(s) |
|---|---|---|---|---|
| 1 | Requirements | BA | Ingests source material (recordings, docs, or a plain description); drafts BRD/PDD/risk register/minutes; extracts INVEST user stories with Gherkin acceptance criteria; writes back to the board; runs its own quality/gap checks | Board writes (Consequential) → baseline requirements (Sign-off) |
| 2 | Design | Architect | Inherits requirements automatically; produces the 8-section architecture package (HLD, LLD, C4 diagrams, API contracts, DB schema, ADRs, tech stack, security checklist); lints its own output; flags anything not traceable to a real requirement | Accept the design (Sign-off, first) → mark epic "Design Complete" (Consequential) |
| 3 | Development | Developer builds, Architect approves | Scaffolds/writes/edits code in a governed sandbox; runs allow-listed build/lint/test commands; never pushes or opens a PR without explicit approval | Push/open PR (Consequential) → accept the implementation (Sign-off) |
| 4 | Code Review | Architect | Reviews the diff with full repo awareness against the requirements/design; runs a Semgrep SAST scan; gives an explicit approve/request-changes/needs-discussion verdict | Accept the review — merge recommendation (Sign-off) |
| 5 | Security | Security Engineer | Runs SCA/SAST/secret scans; builds the SBOM; issues the mandatory PASS/FAIL/CONDITIONAL verdict that gates Deployment | Security sign-off (Sign-off, mandatory) |
| 6 | Testing | QA/Tester | Generates and runs the test plan across all test types in a sandbox | Run suites (Consequential) → accept results (Sign-off) |
| 7 | Deployment | DevOps Engineer | Stages the release package; aggregates test + security verdicts into a go/no-go | Release sign-off (Sign-off, mandatory, first) → trigger deploy (Consequential) |
| 8 | Documentation | BA (auto-accept; Project Admin fallback) | Produces first-release doc set; docs PR is a distinct gated action | Docs PR (Consequential) → acceptance (Sign-off, automatic, override exists) |

**Track 2 — Enhancement & Support** runs this exact same portfolio, unchanged — the one
genuine case of two tracks sharing agents (§1.4). The only behavioral differences: Design
is skipped entirely unless the change actually needs one; Requirements produces an impact
assessment against an existing system rather than first-release requirements;
Documentation updates the existing runbook/knowledge article instead of producing a
first-release doc set. Every gate and approver is exactly as in Track 1.

### Portfolio 2 — Code Modernization (Track 3 only, target design, not yet built)

Its own 10-agent portfolio, entirely independent of Portfolio 1 — no agent here is a
reference to or reuse of a Greenfield/Brownfield agent, even where one shares a name and
owning role (§1.4's naming convention). Shaped like Portfolio 1 with two additional
stages a greenfield build never needs:

| # | Agent | Owner | What it does | Its gate(s) |
|---|---|---|---|---|
| 1 | Requirements (migration-intent mode) | BA | Captures why the modernization is happening, scope, constraints, success criteria | Board writes (Consequential) → baseline the migration-intent brief (Sign-off) |
| 2 | **Discovery & Assessment** | Architect | Clones and reads the legacy repo directly; maps the dependency graph; flags EOL/vulnerable dependencies; scores every module for migration risk | Accept the assessment as planning baseline (Sign-off) |
| 3 | Design | Architect | Decides the target architecture from the assessment; records the rewrite-vs-strangler-fig and target-stack decisions as ADRs | Accept the target design (Sign-off) |
| 4 | **Strategy** | Architect | Sequences the migration path — which modules move in which order (lowest-risk first), equivalence criteria per module | Accept the migration plan (Sign-off) |
| 5 | Development | Developer builds, Architect approves | Executes the plan module-by-module using code-mod/upgrade tooling where mature, LLM-assisted rewriting otherwise; preserves external behavior by design | Push/open PR (Consequential) → accept the migrated module (Sign-off) |
| 6 | Code Review | Architect | Reviews each migration diff against the target design and equivalence criteria | Accept the review (Sign-off) |
| 7 | Security | Security Engineer | Same scan stack as Track 1, plus a check for secrets/insecure patterns carried over verbatim from the legacy code | Security sign-off (Sign-off, mandatory) |
| 8 | Testing | QA/Tester | Runs differential/equivalence testing — executes legacy and modernized code against identical inputs and diffs the outputs | Run tests (Consequential) → accept validation results (Sign-off) |
| 9 | Deployment | DevOps Engineer | Manages a phased or full cutover, not a single atomic switch; parallel-run window before legacy is switched off | Release sign-off (Sign-off, mandatory) → trigger cutover (Consequential) |
| 10 | Documentation | BA (auto-accept; PA fallback) | Produces the cutover pack: updated SDD, old-to-new traceability map, equivalence evidence, decommission note | Docs PR (Consequential) → acceptance (Sign-off, automatic) |

### Portfolio 3 — RPA & Infrastructure Migration (Track 4 only, target design, not yet built)

Its own 8-agent portfolio, independent of Portfolios 1 and 2. Four agents are exclusive
to the three migration flavors this track flexes across — RPA-to-RPA, RPA-to-Code, or
Infrastructure migration, mixable within the same wave — and four more round out the
shape (Requirements up front, a Security gate, Deployment, Documentation), each its own
agent even though the names and owning roles echo Portfolio 1's.

| # | Agent | Owner | What it does | Its gate(s) |
|---|---|---|---|---|
| 1 | Requirements | BA | Pins down which migration flavor(s) this wave is, in-scope items, wave/batch plan | Board writes (Consequential) → baseline the migration-intent brief (Sign-off) |
| 2 | **Discovery & Assessment** | Architect | Bot/Process mode: parses an exported `.aapkg`, scores risk, flags credentials for re-provisioning. Infrastructure mode: ingests IaC or a manual inventory, assesses each component's disposition (retain/resize/re-platform/retire) | Accept the assessment as planning baseline (Sign-off) |
| 3 | **Migration Mapping** | Architect | Normalizes into a platform-neutral intermediate representation, then maps it — to a target RPA platform, to code constructs (calling a real API where one exists instead of re-automating a UI), or to a target infrastructure component | Resolve an ambiguous mapping (Consequential) → accept the migration plan (Sign-off) |
| 4 | Development | Developer builds, Architect approves | Generates the target-platform project / application code / IaC from the resolved mapping; validates structurally before any deploy; never auto-carries credentials — flags them for manual re-provisioning | Deploy/apply live (Consequential, mandatory) → accept the migrated item (Sign-off) |
| 5 | Security (pre-go-live gate) | Security Engineer | Confirms flagged credentials are actually re-provisioned and correctly scoped; scans generated code or reviews IaC for network/IAM/encryption posture, depending on flavor | Security sign-off (Sign-off, mandatory) |
| 6 | **Validation** | QA/Tester | Runs the legacy and migrated item side by side against identical input and diffs the real results — the parallel-parity run | Run parallel validation (Consequential) → accept item as cutover-ready (Sign-off) |
| 7 | Deployment | DevOps Engineer | Operates at the wave/program level — sequences which items go live in what order, aggregates Validation + Security results into a go/no-go, plans legacy decommissioning | Cutover sign-off (Sign-off, mandatory) → trigger cutover (Consequential) |
| 8 | Documentation | BA (auto-accept; PA fallback) | Produces the migration summary and decommission plan for the legacy versions | Docs PR (Consequential) → acceptance (Sign-off, automatic) |

### Portfolio 4 — Data Engineering (Track 5 only, target design, not yet built)

Its own 6-agent portfolio, independent of the other three. Five of its agents echo
names and owning roles from Portfolio 1 (Requirements, Security, Testing, Deployment,
Documentation), plus one dedicated agent that doesn't exist anywhere else for the
data-specific work. No Design, Development, or Code Review stage — there's no
application code being built.

| # | Agent | Owner | What it does | Its gate(s) |
|---|---|---|---|---|
| 1 | Requirements (data-intent mode) | BA | Captures the data/analytics requirement — source system(s), target (warehouse/lake/reporting), quality bar, cost baseline | Board writes (Consequential) → baseline the data-intent brief (Sign-off) |
| 2 | **Data Engineering** | Data Engineer | Discovers/profiles sources (read-only); generates connector configs (Snowflake, Redshift, data lakes, JDBC/ODBC); auto-generates ELT/ETL pipeline code with incremental-load logic; produces performance and cost-optimization recommendations; captures lineage and per-field data-classification tags | Register connector, deploy pipeline (Consequential) → accept pipeline as production-ready (Sign-off) |
| 3 | Security (PII/data-classification mode) | Security Engineer | Same scan stack as Track 1, emphasis shifted to verifying field classification tags and connector credential scoping | Security sign-off (Sign-off, mandatory) |
| 4 | Testing (data-quality mode) | QA/Tester | Runs the data-quality test scaffold — row-count reconciliation, null/duplicate/outlier checks, schema-drift detection, referential-integrity checks | Run checks (Consequential) → accept results (Sign-off) |
| 5 | Deployment | DevOps Engineer | Stages and deploys a pipeline/orchestration job (e.g. an Airflow DAG); aggregates data-quality + security verdicts into go/no-go | Release sign-off (Sign-off, mandatory) → trigger/schedule (Consequential) |
| 6 | Documentation | BA (auto-accept; PA fallback) | Produces pipeline docs, a lineage diagram, and a data-classification register | Docs PR (Consequential) → acceptance (Sign-off, automatic) |

---

## Part 4 — Technical architecture (for engineers)

### 4.1 What exists today — full survey findings

**Read this whole section as an inventory of files to look at and mostly start over
from, not a list of working features.** Nothing described below — the agent registry,
any of the 8 agent routers, the Orchestrator — has been verified to actually work
end-to-end or tested against the rules in this document. The one exception is the RBAC
machinery specifically (the `require_permission` dependency pattern, the mirror
contract, the permission catalog) — that infrastructure was built and tested earlier
this session for a different feature and is genuinely solid; it's the pattern this
document proposes extending. The agents and the Orchestrator are not in that category.

- **Backend agent registry** (`backend/config/agent_registry.py:34-188`) is a flat,
  track-unaware dict listing 8 agent ids: `requirements, design, development, testing,
  code_review, security, deployment, documentation`. It is a list of names and routing
  entries, not evidence those 8 agents function correctly — that has not been verified.
  No `data_engineering` or any Track 3/4-only agent exists in the backend at all —
  matches the PRD's own "target design, not yet built" labeling for those tracks.
- **No per-agent access check exists anywhere in the backend today.** Every agent's
  FastAPI router (`requirements_agent_api.py`, `design_architecture_agent_api.py`,
  `development_agent_api.py`, `testing_agent_api.py`, `code_review_agent_api.py`,
  `security_agent_api.py`, `deployment_agent_api.py`, `documentation_standalone_api.py`,
  plus `orchestrator_router`/`copilot_router`) is mounted in
  `backend/process_api.py:878-904` behind the identical
  `require_permission("artifact:view")` — the universal read floor every project role
  holds. `POST /runs` (`backend/shared/routers/runs.py:67-72`), the entry point that
  starts a run and picks which agents participate via `RunCreateIn.active_agents`/
  `skip_agents`, is guarded only by `run:create` — again, no per-agent check anywhere.
  This gap needs closing regardless of whether the agent code behind each router turns
  out to be reusable or gets rebuilt from scratch.
- **`agent:invoke` already exists as a permission string** in the catalog
  (`backend/shared/authz/permissions.py:223`) and every delivery role already holds it —
  but it is never referenced by any `require_permission(...)` call anywhere in the
  codebase. Today it is a flag that does nothing.
- **A per-project, per-agent override mechanism already exists — and is dead code.**
  `backend/shared/routers/project_scoped.py:87-186` implements full CRUD
  (`GET/PUT/DELETE /projects/{id}/agent-access-overrides`) over a table keyed by
  `(project, role, phase/agent, involvement)`, correctly gated by `member:manage` +
  `assert_can_administer_project`. Nothing else in the backend reads this table — not
  `POST /runs`, not any agent router — so today it has zero effect on who can actually
  invoke an agent. This is exactly the mechanism §1.6's override step needs; it just
  needs to be read at enforcement time, not only written to.
- **The frontend already has a full ownership matrix**
  (`frontend/lib/roles.ts:337`, `AGENT_OWNERSHIP`) covering **13** agent names — the 8
  real ones, plus Discovery, Strategy, Migration Mapping, Validation, and Data
  Engineering, which already have frontend pages and labels (`frontend/lib/agents.ts:
  21-47`) but no backend implementation behind them.
  `roleAgentSplit()` (`frontend/lib/agent-access.ts:53-68`) uses this matrix to compute
  which tiles lock — this is UI-only; nothing server-side enforces it, so a direct API
  call bypasses it entirely today.
- **Two parallel role/permission systems coexist in the frontend.**
  `effectivePlatformRole()` (`frontend/lib/auth/effective-role.ts:49`) drives the
  agent-tile logic above. A separate, coarser `RequireRole`/`Capability` gate
  (`frontend/components/auth/require-role.tsx`, `frontend/lib/auth/capabilities.ts`) is
  used elsewhere in the app. The two can drift — the 13-vs-8 agent name mismatch above is
  one visible symptom of that drift.
- **`backend/shared/authz/permissions.py` already documents an explicit "mirror
  contract"** to the frontend — the established pattern for keeping a
  backend-authoritative catalog and a frontend copy in sync. This is the natural
  precedent to extend for the agent catalog, rather than inventing a new pattern.
- **Files named "Orchestrator" already exist** at both
  `frontend/app/(app)/orchestrator/page.tsx` (global) and
  `frontend/app/(app)/projects/[id]/orchestrator/page.tsx` (per-project), rendering
  `components/orchestrator/cockpit.tsx`. What's there today walks the project's full
  agent roster in hand-off order with no role-based filtering inside the cockpit — but,
  as with the agent routers above, this should be treated as an unverified placeholder
  to properly build and test (§2.3), not a working cockpit that just needs a filter
  bolted on.

### 4.2 Data model

The key structural decision, following §1.4: **agent definitions belong to a portfolio,
not to a shared global catalog.** Two new pieces of backend config:

- **`TRACK_PORTFOLIOS`** — `dict[Track, list[AgentDefinition]]`, one entry per track,
  each list ordered exactly as tabulated per-portfolio in Part 3. Each `AgentDefinition`
  carries an id (unique *within its own portfolio*, not globally — e.g. `security` can
  legitimately exist in more than one portfolio's list, as its own independent entry
  each time), a display name, its owning role, and a `backend_available` flag (false for
  every agent in Portfolios 2–4 until it's actually built). `Track.GREENFIELD` and
  `Track.ENHANCEMENT` are the one pair of keys that point at the *same* underlying list
  object — the single genuine case of sharing (§1.4) — every other track's list is
  authored independently, even where entries echo another portfolio's naming/ownership
  convention.
- **`projects.track`** — a new column (enum of the 5 tracks), set once at project
  creation, never edited afterward. A project's agent list is simply
  `TRACK_PORTFOLIOS[project.track]`.

For the admin-override mechanism (§1.6, point 5 — both grains), the existing
`agent_access_overrides` table gets one schema change: add a nullable `user_id` column
alongside the existing `role` column. Exactly one of (`role`, `user_id`) is set per row:
a role-level override has `role` set and `user_id` null; a person-level override has
`user_id` set and `role` null. Both are already scoped to one project and one agent, so
this is an additive, non-breaking change to a table that is currently unread (dead code
— see §4.1) rather than a redesign.

### 4.3 Backend enforcement

A new dependency factory, `require_agent_access(agent_id)`, follows the exact shape of
the existing `require_permission(...)` factory already used throughout
`process_api.py`. For a given caller it resolves, in order:

1. Does their role own or use this agent by default, per that project's own portfolio
   (§4.2)?
2. Is there a project-level **person-level** override row naming this exact caller for
   this agent?
3. Is there a project-level **role-level** override row naming the caller's role for
   this agent?
4. Is the caller a Project Admin (fallback owner on every agent in-portfolio, always)?
5. Otherwise, deny with a clear reason — matching the PRD's own "Error (denied
   access)... deny-by-default; legible failure" state (§37).

Notably, `admin:*` does **not** bypass this check. Org/BU Admins genuinely have zero
agent access by design (§1.4) — this is a distinct axis from the existing
permission-wildcard shortcut used elsewhere in the RBAC system, and must not be
conflated with it.

This replaces the blanket `artifact:view` dependency on each individual agent router
with `require_agent_access("<that agent's id>")`, and adds an equivalent check inside
`POST /runs`, validating every id in `active_agents` against the caller's allowed set
before a run is created. The Orchestrator's stage-advance action gets its own separate
check (Project-Admin-only, per §2.3) — independent of, and in addition to, the per-agent
check that still applies to any individual chat turn taken from inside it.

### 4.4 Frontend: single source of truth

Extend the existing mirror-contract pattern (§4.1) to cover the four portfolios —
`AGENT_OWNERSHIP` in `frontend/lib/roles.ts` becomes a reviewed-together mirror of
`TRACK_PORTFOLIOS`, keyed the same way (per-track, per-portfolio), rather than an
independently hand-maintained flat table, closing the 13-vs-8 drift at the source. For
agent-gating specifically, `effectivePlatformRole()` becomes the one role signal
consulted — the coarser `RequireRole`/`Capability` system stays for whatever non-agent
gating it already does elsewhere in the app, but stops being a second, possibly
disagreeing opinion on agent access specifically.

---

## Part 5 — Building (or properly rebuilding) an agent, the repeatable pattern

This checklist applies twice over: to the 24 agent definitions across Portfolios 2, 3,
and 4, which don't exist in the backend at all yet, **and** to Portfolio 1's 8 agents,
where old, unverified code exists and needs to go through the same steps properly — not
be assumed done because a file with the right name is already there. Following the same
checklist for both is what stops "looks like Portfolio 1's Security Agent" from being
either a false assumption of working code or, later, an accidental shared dependency
between portfolios that should stay independent (§1.4):

1. Add the agent's entry directly to its own portfolio's list in `TRACK_PORTFOLIOS`
   (`backend_available: false` until step 5) — never by referencing or importing another
   portfolio's entry, even for an agent with a matching name and owning role.
2. Place it at the right hand-off position within that portfolio's own ordered list
   (Part 3's per-portfolio tables).
3. Build the agent itself — a standalone LangGraph agent-and-tool graph, per the PRD's
   own description of what's common to every agent (§20.2): read-only on the repository
   except for explicit, gated write actions; extends its native tools at runtime with
   per-agent skill tools and BYO MCP tools; reads prior-stage outputs from the shared
   artifact store.
4. Build its FastAPI router using `require_agent_access("<id>")` from the start (§4.3) —
   never the old blanket `artifact:view` pattern, so nothing new ships with the same gap
   this document exists to close.
5. Mount the router in `process_api.py`.
6. **Test it end-to-end before flipping the flag** — the access checks from §4.3, the
   agent's actual Safe/Consequential/Sign-off behavior, and (once its upstream
   dependencies are real) a genuine hand-off from the agent before it. Only once that's
   actually verified does `backend_available: true` go on that portfolio's entry. This
   single flag is what turns the frontend tile from "Coming soon" to real — nothing else
   needs to change on the frontend side for that agent to appear correctly under its own
   track, with the right lock states, automatically. Flipping it without step 6 done is
   exactly the state Portfolio 1 should be treated as being in today, and exactly what
   this checklist exists to avoid repeating.

---

## Part 6 — Open questions and risks found during research

- **PRD ownership-matrix gaps.** §14.7's table (Portfolio 1/Appendix) shows DevOps
  Engineer with no access at all to the Data Engineering agent, and Data Engineer with no
  access to the Deployment agent — that specific table is Portfolio 1's own chart and
  doesn't directly constrain Portfolio 4 (Data Engineering, its own independent
  portfolio), but the same question resurfaces there: Portfolio 4 includes both a Data
  Engineering and a Deployment stage, and it seems odd a Data Engineer couldn't at least
  view the Deployment agent for their own portfolio's pipeline. Worth confirming with
  whoever owns the PRD before each portfolio's ownership chart is finalized in code.
- **Tracks 3 and 4 are explicitly "target design" in the PRD itself** — not partially
  built, not stubbed, genuinely not started. Parts 1–3 of this document describe the
  intended end state for all 5 tracks; Parts 4–5 are only actually actionable today for
  Track 1/2's 8 agents plus the data-model/enforcement scaffolding, which is written to
  extend cleanly once Tracks 3/4/5's agents actually get built.
- **Two parallel frontend role systems** (§4.1) are wider tech debt than just agent
  access. This document only proposes resolving their disagreement *for agent-gating
  purposes*; a full unification, if wanted, is a separate piece of work.
- **The 8 existing Portfolio 1 code files should be assumed broken/non-functional until
  proven otherwise** — this document does not treat "code exists" as "feature works" for
  any of the 8 agents or the Orchestrator (see the note at the top of the document), and
  Part 5's checklist applies to all 8 of them, not only to new agents. Whether any given
  file ends up salvageable during that rebuild, versus being rewritten outright, is a
  per-agent implementation decision each owner makes while going through the checklist —
  it doesn't change the architecture above, but it does mean nothing here should be
  reported as "already working" without having actually gone through step 6.

---

## Appendix — PRD §14.7 ownership matrix, verbatim structure

The authoritative role × agent involvement chart for the 8 core (Track 1/2) agents, as
specified in the PRD. **Owner** = approves Consequential actions and Sign-offs for that
agent. **Build** = does the hands-on work; a separate Owner approves it. **Use** =
can chat/run Safe capabilities only. **—** = no involvement by default.

| Role | Requirements | Design | Development | Code Review | Security | Testing | Deployment | Documentation | Data Engineering |
|---|---|---|---|---|---|---|---|---|---|
| Project Admin | Owner (fallback) | Owner (fallback) | Owner (fallback) | Owner (fallback) | Owner (fallback) | Owner (fallback) | Owner (fallback) | Owner (fallback) | Owner (fallback) |
| BA | **Owner** | Use | Use | Use | Use | Use | Use | Use | Use |
| Architect | Use | **Owner** | **Owner** | **Owner** | Use | Use | Use | Use | Use |
| Developer | Use | — | Build | Requests review | Use | Use | — | Use | Use |
| QA / Tester | Use | — | Use | — | Use | **Owner** | — | Use | Use |
| Security Engineer | Use | Use | Use | Use | **Owner** | — | Use | — | Use |
| DevOps Engineer | — | — | Requests tooling | — | Use | Use | **Owner** | Use | — |
| Data Engineer | Use | Use | — | — | Use | Use | — | Use | **Owner** |

Off-pipeline capabilities, for completeness (not agent access, but adjacent
capabilities the PRD groups in the same section): member onboarding/role assignment
(project) and connector selection → Project Admin. Connector requests and account
linking → any contributor. Connection registration, unit budget, and project creation →
Business Unit Admin. Organization budget and org-wide policy → Organization Admin.
