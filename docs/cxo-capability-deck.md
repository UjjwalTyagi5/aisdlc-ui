# Capability deck — ten slides for a CXO audience

**What this is.** The content for a ten-slide capability deck about the Agentic SDLC Platform,
written to be presented to a CIO, CTO, CISO, CFO or Head of Delivery in any industry, and to move
them to a decision about AI-led delivery. Each slide gives the headline that goes on screen, the
on-screen content, what to say, and the executive question it answers.

**Its sibling.** `docs/cxo-capability-deck-client.md` is the **client edition**: the same ten
slides carrying only what is built and demonstrable, with the ledger below and every
not-yet-built mention removed. Present that one. Read this one first, so you know where its
edges are.

**Where the content comes from.** Every capability claim below is traceable to something the
platform has actually done on a real run — `Demo_Video_Script.docx` (fifteen clips, each marked
*Ran* or *Rehearse*), `Demo_Narration_Script_Fast.docx`, `full fflow.md` and `DEMO_HAPPY_PATH.md`
— or to code in this repository. The PRD supplies the governance *vocabulary* (capability classes,
scope hierarchy, the five tracks); it also describes a great deal that is not built yet, and none
of that is presented here as if it were. The ledger below draws the line.

**Running time.** 20–25 minutes for the ten slides, 15 for questions. Slides 5, 6 and 8 win the
room. Cut to ten minutes: 1, 2, 3, 5 and 10.

**Two rules for using it.**

1. **Anything in ⟨angle brackets⟩ is a blank you fill in for the client.** Never present it as
   delivered.
2. **The deck quotes no productivity percentage, anywhere, on purpose.** The product's own scope
   statement rules out "claims of productivity improvement without pilot-specific baselines and
   independently agreed measurement." Say that out loud on slide 10 — in a market full of
   unfalsifiable numbers, refusing to give one is the most credible thing in the deck.

**Swapping the industry.** Only slide 4 carries industry examples. Replace that column and the
deck travels from a bank to a hospital group to a manufacturer without another edit.

---

## The claims ledger — read this before you present

The narration script's own closing line is the honest summary: **eleven agents built today, four
more on the way.** Keep the deck on the left-hand column.

| Area | Built and demonstrated | Designed, not built — do not imply otherwise |
|---|---|---|
| **Agents** | Nine on the greenfield/enhancement roster: Requirements, Design, Project Manager, Development, Code Review, Testing, Security, Deployment, Documentation — plus the Orchestrator that routes across them, and two modernization agents (Migration Intent, Dependency & Risk) | The rest of the modernization roster; every Track 4 (RPA) and Track 5 (data engineering) agent |
| **Tracks** | **Track 1 Greenfield** — filmed end to end. **Track 2 Enhancement/Brownfield** — same nine agents, entered at whichever stage the change needs. **Track 3 Modernization** — its first two agents | Tracks 4 and 5 exist as a project type and inherit the Track 1 roster; their own agents are not built |
| **Approvals** | Every agent output is a document: raised by its producer, decided by a named approver, recorded against the project, with one queue and one trail | Risk tiers R1–R4, policy packs, approval quorums |
| **Autonomy** | Human-directed: the agent proposes, a person approves, the action then runs | Anything unattended. L4/L5 on the PRD's ladder are future capability |
| **Access** | Organization → Business Unit → Project, granted downward, reaching **the individual agent** per person. Connector credentials are per person | The Product/Application tier in the PRD's hierarchy |
| **Cost** | Every model call logged with tokens and cost; Cost page groups by agent and model; nested budgets org → unit → project, enforced **fail-closed** before and during a run | Automated alert delivery (email/Teams) on a budget breach |
| **Identity** | Local accounts; the Organization Admin can hold Entra SSO configuration, MFA requirement and session timeout | Federated SSO login and SCIM provisioning — schema-ready, not wired |
| **Integration** | Azure DevOps and Confluence, both demonstrated live. Shipped connectors also cover Azure Repos/Pipelines, GitHub Issues/Actions, Jira, SharePoint, Teams, Slack, SonarQube, Figma, and BYO tools over MCP | Anything not on that list |
| **Agent Studio** | Behaviour and skills scoped organization → Business Unit → project → personal sandbox, resolved most-specific-first, versioned, lint-gated, publish and rollback, propose-to-the-owner for approval | Cross-organization skill marketplaces; automatic promotion |
| **Deployment** | One Linux VM, no Docker required, documents on your disk or your Azure Blob | Managed SaaS |

**Known rough edges, if someone asks.** Publishing the *architecture* to Confluence failed on
15 September and is not in the demo cut — publishing is filmed once, from Requirements, and it
works. Epics-and-stories to the board changed recently and is marked *rehearse*. Say these plainly
if pushed; the deck does not depend on either.

---

## Slide 1 — Title

> ### Turn demand into trusted production change.
> **An agentic SDLC platform your auditors, your CISO and your CFO can live with.**

**On screen**

- The platform name and ⟨your firm's⟩ mark.
- The proposition, in one line: *specialised AI agents across the full software lifecycle, with
  human accountability, traceability and cost control kept intact.*
- Four chips along the bottom: **An agent for every stage** · **Nothing moves without an approval**
  · **One project record** · **Every model call priced**.

**Say it like this.** "I'm not here to show you another code-generation assistant — you have
those. This is the layer above them: what the agents are allowed to do, who signs off, what
evidence survives, and what it costs. Without that layer, AI in delivery stays a pilot forever.
Everything I show you today is from a run that actually happened, on a real repository and a real
Azure DevOps board."

**Answers:** *Why is this different from the copilot we already bought?*

---

## Slide 2 — The problem

> ### Copilots made engineers faster. They did not make delivery faster.

**On screen**

- **Assistance is everywhere; the flow from demand to production is still manual, late and
  unevidenced.**
- **Context is fragmented.** Requirements in one tool, code in another, test evidence, security
  findings, pipelines and documentation in four more. An agent that sees only one of them is
  guessing about the rest.
- **Control arrives last.** Security and release review what has already been built, so the
  conversation becomes a negotiation instead of a gate.
- **So adoption stops at the pilot** — not because the models are weak, but because nobody can
  answer three questions.

> **The three questions that stall every AI rollout:**
> **Who approved it? What evidence is there? What did it cost?**

**Say it like this.** "Ask your teams what the last AI pilot produced and you'll hear about
velocity in pockets. Now ask whether the last thing an agent wrote is traceable from a signed
requirement through a tested, scanned, approved release — and whose name is on each step. That gap
isn't a model problem. It's a platform problem."

**Answers:** *Why hasn't our AI investment shown up in delivery metrics?*

**Visual.** Two lanes: "assistance in pockets" (scattered tools, dotted lines, control at the far
end) against "governed flow" (one spine, gates drawn as named people).

---

## Slide 3 — What it actually is

> ### One project record, an agent for every stage, and a person at every gate.

**On screen — four facts that hold everywhere in the product**

| | |
|---|---|
| **1. An agent for each stage of delivery** | Requirements, Design, Project Manager, Development, Code Review, Testing, Security, Deployment, Documentation — plus an Orchestrator that routes a request to whichever agent owns it, and two more for legacy modernization |
| **2. Everything an agent produces is a document** | Raised for approval by whoever produced it, decided by a named approver, recorded against the project. One queue to decide in; one trail of who approved what, and when |
| **3. Each stage consumes what the last one had approved** | The architecture is written from the *approved* requirements; the code from the *approved* architecture; the handover from all of it. Drafts do not propagate |
| **4. Access reaches down to the individual agent** | A project admin onboards each role and grants the specific agents that person may open. The Business Unit approves the project itself before any work starts |

**And what it is deliberately not**

- Not a replacement for your systems of record — your repository, board, wiki and pipelines stay
  where they are.
- Nothing unattended: the agent proposes, a person approves, and only then does the action run.
- No productivity claim without your baseline.

**Say it like this.** "Four facts, and the second is the one to hold on to. Every output is a
document with an approver's name on it, and the next agent only reads what was approved. That is
what makes the trail at the end worth anything."

**Answers:** *What am I actually buying, and what does it leave alone?*

---

## Slide 4 — Coverage

> ### Five delivery tracks by design. Two carry the full roster today.

**On screen**

| Track | Status | What it does | Typical industry demand |
|---|---|---|---|
| **1 · Greenfield** | **Built — demonstrated end to end** | Discovery call to handover through all nine agents | A new digital journey — ⟨customer onboarding in BFSI, a patient portal in healthcare, a D2C storefront in retail⟩ |
| **2 · Enhancement & support (brownfield)** | **Built — same nine agents** | Entered at whichever stage the change actually needs, rather than from the top | The permanent majority of the portfolio — ⟨peak-season defects, claims fixes, plant-floor change requests⟩ |
| **3 · Modernization** | **First two agents built** | *Migration Intent* captures why the modernization is happening, from what to what, and what success looks like. *Dependency & Risk* clones the legacy repository read-only, maps dependencies, flags what is past end of life, and scores each module's risk | ⟨Core banking, policy administration, public-sector legacy estates⟩ |
| **4 · RPA & infrastructure migration** | Designed | Bot estates and datacentre waves | ⟨Shared-services and GCC bot estates⟩ |
| **5 · Data engineering** | Designed | Pipelines, lineage, data quality, cost optimization | ⟨Warehouse and lakehouse programmes⟩ |

**The point, in one line under the table:** *one control plane, one set of roles, one approval
trail and one cost model — so this is a platform decision, not five tool decisions.*

**Say it like this.** "I'll be straight about the status column, because it is the part most decks
lie about. Greenfield and enhancement are built and I can show you a full run. Modernization has
its first two agents — the ones that decide whether a modernization is worth starting. Four and
five are designed, on the same control plane, and not built. If your pain is in track four, I'd
rather tell you that now than in month three."

**Answers:** *Does this cover my real portfolio — and what is actually ready?*

---

## Slide 5 — The run, end to end

> ### Discovery call to handover — this is one real project, not a montage.

**On screen** — the thread, left to right, with what each stage produced:

| Stage | Owner | What it did on the real run |
|---|---|---|
| **Requirements** | BA | Turned the recorded discovery call into a BRD in the house template; after approval, published it to Confluence and wrote the epics and user stories straight onto the Azure DevOps board, hierarchy intact |
| **Design** | Architect | Wrote the architecture from the approved BRD, inside the technology stack the organisation allows — HLD and LLD, API contracts, database schema, and the decisions behind them |
| **Project Manager** | Project admin | Turned the design into a schedule — work breakdown, estimates, sprints, who is on what — built from what is approved, not from a wish list |
| **Development** | Developer | Built the codebase from the approved documents, pushed it to a branch in Azure DevOps, then read the board and picked up the stories assigned to it |
| **Code Review** | Architect | Read **every file on the branch** — 14 reviewable files, 325 lines — mapped the code back to the approved requirements and architecture, ran secret, static and dependency scans, and returned findings citing file and line. The pull request itself was empty, **and the review said so** |
| **Testing** | QA | Wrote **unit, functional and API** cases in one pass, each an Excel workbook read and **approved before anything ran**. Unit tests executed in the repository. Functional cases drove the running application **in a browser**, step by step, keeping a screenshot on failure. API cases called every endpoint in order, passing values from one case into the next |
| **Security** | Security engineer | Scanned the branch for secrets, code weaknesses and vulnerable dependencies: 13 vulnerabilities, 9 high or critical, signed off with the risk recorded as accepted |
| **Deployment** | DevOps | Staged the release — container image, manifests, pipeline, runbooks — and returned a decision: **conditional go** |
| **Documentation** | Architect / BA | Compiled the handover from everything upstream: scope, access, work in flight, known risks |

> **The moment to hold on:** a generated functional case found a **real defect** — the create page
> never showed its validation error. Nobody wrote that test.

**Three refusals worth naming out loud**

- The security agent **cannot quietly pass a critical finding**; a sign-off that leaves one out is
  refused.
- The deployment agent marks what it did not measure **unknown** — unknown is never counted as
  passed.
- Approving the deployment report **deploys nothing**. The decision and the action are separate.

**Say it like this.** "One project, one thread. And notice where it was unflattering to itself: the
pull request was empty and the review said so; nine findings were high or critical and the sign-off
recorded them rather than burying them; a test we didn't write found a bug we didn't know about.
An agent that only ever agrees with you is not an assurance system."

**Answers:** *Show me it works on real systems, not on slides.*

---

## Slide 6 — Governance

> ### The agent proposes. A named person decides. The record keeps both.

**On screen**

**Every action an agent can take is one of three things**

| Class | What it covers | Control |
|---|---|---|
| **Safe** | Read, analysis, drafting inside the platform | Runs immediately for an authorised user; logged |
| **Consequential** | Any write to your systems — a branch, a work item, a wiki page, a pipeline | Halts at a gate until a person approves it |
| **Formal sign-off** | Acceptance of quality, security or release risk | A named approver, on the evidence, recorded against the project |

**What that looks like in the product, today**

- Approval is the **same everywhere**: raised by whoever produced the work, from the chat or from
  the document; the approver sees what was raised and by whom; the approved document becomes the
  project's record and carries "Approved by".
- **One queue** to decide in, **one trail** of who approved what and when, and a history that keeps
  every generation and run — who ran it, and against which branch.
- **Access reaches the individual agent.** Two people in the same role need not have the same
  agents. The Business Unit approves the project before any work starts.
- **Autonomy is human-directed today** — the agent proposes, a person approves, the action then
  runs. Unattended operation is on the roadmap with eligibility criteria still open, and is not
  offered.

**Say it like this.** "This is the slide your CISO came for, and the honest part is the last line.
There is no unattended mode. Anyone selling you autonomous production change today is selling you
an incident. What this gives you instead is that every consequential step stopped, in front of a
person who could have said no — and the record proves which one of them did."

**Answers:** *What can the AI do without a human, and who is accountable when it's wrong?*

---

## Slide 7 — Control, isolation and audit

> ### Access that matches your org chart, and a record that outlives the project.

**On screen**

- **Three levels, granted downward.** Organization → Business Unit → Project, and no administrator
  can grant a permission they do not themselves hold.
- **The Business Unit is the blast radius.** Its own budget, its own connected ⟨Azure DevOps /
  Jira⟩ instance, no visibility into the others. Two separate Jira sites are two Business Units.
  A group that must wall off subsidiaries can run isolated Organizations in one install.
- **Governance roles govern; delivery roles deliver.** Organization and Business Unit Admins set
  structure, budget and policy — and hold no agent access at all.
- **Tools are granted top-down; identity is supplied bottom-up.** The unit registers the connector;
  each person links **their own** account; the agent acts as that person, with that person's
  permissions, in your systems. There is no shared production token, and on the demo project each
  persona who touches a branch has their own Azure DevOps connection.
- **Isolation is enforced in the database**, by row-level security on every tenant-scoped table —
  not only in application code.
- **An audit trail of access, approvals, runs, role changes and configuration changes**, each with
  actor, timestamp and scope, append-only by database privilege rather than by application
  convention.

**Say it like this.** "Two details worth pausing on. The agent never gets its own super-account in
your estate — it acts as the person driving it, so your existing tool-side permissions still apply.
And isolation is enforced by the database, which means a defect in our application code cannot
quietly leak one client's project into another's."

**Answers:** *Can I give this to a regulator, and what is the blast radius if something goes wrong?*

---

## Slide 8 — The economics

> ### Every model call is priced and attributed — and the cap is enforced, not advisory.

**On screen**

- **Every model call is logged** with its tokens and its cost, per project, per agent, per model.
  The Cost page answers "what did this stage cost" as a fact, not an estimate.
- **Budgets nest and the top tier wins.** A Business Unit cannot exceed the organization's cap for
  it; a project cannot exceed its unit's.
- **The cap is enforced fail-closed** — checked before a run starts and again at each model
  resolution mid-run. An exhausted scope stays blocked until a human raises the figure. It is not a
  dashboard you read after the money is gone.
- **Your models and your keys.** Every agent page carries a model picker, so you choose which model
  each stage runs on; keys are held per organization, encrypted, in your database — never in a
  config file, never ours. Anthropic, OpenAI, Google, xAI and others through one interface, so
  switching provider is configuration, not a project.
- **Optional self-hosted tracing** gives the call-by-call view — deployed inside your estate, with
  no third-party analytics in the path.

> **And the discipline:** we do not quote you a productivity percentage. We instrument the run so
> that **your** baseline produces **your** number — slide 10.

**Say it like this.** "Three questions get asked in every one of these meetings: what does it cost,
where does the money go, and what stops it running away. The third is the one most products answer
with a chart. Here the budget check runs before the agent starts and again every time it reaches
for a model, and it fails closed."

**Answers:** *What will this cost me, and how do I stop it running away?*

---

## Slide 9 — Fits your estate

> ### Deployed in your estate, wired to your tools, tuned to your standards — without a fork.

**On screen — three columns**

**Deployed where you decide**
- Client-deployed and single-tenant. One Linux VM runs the whole platform: application, PostgreSQL,
  Redis, and a web front door.
- **Docker is not required** anywhere in the platform.
- Only **443** is exposed; everything else listens behind the front door.
- Documents live on your disk or in your ⟨Azure Blob⟩ tenancy. Your code and documents do not leave
  your boundary except to the model provider you approved.

**Wired to what you already run**
- **Demonstrated:** Azure DevOps (boards, repos, pull requests) and Confluence.
- **Also shipped:** Azure Repos and Pipelines, GitHub Issues and Actions, Jira, SharePoint,
  Microsoft Teams, Slack, SonarQube, Figma — and bring-your-own tools over MCP.
- **Identity today:** local accounts, with the Organization Admin holding Entra SSO configuration,
  an MFA requirement and session timeout. Federated login and SCIM provisioning are the next step,
  and the data model is already shaped for them.

**Tuned to how you work — Agent Studio**
- The behaviour instructions and reusable skills behind each agent, scoped **organization →
  Business Unit → project → a personal sandbox**, resolved most-specific-first.
- A builder tunes an agent in their **own sandbox**, where it affects only their own runs, then
  proposes it to the scope's owner to publish. Versioned, lint-gated, with rollback by publishing
  an earlier version.
- New projects start from the unit's defaults, so a standard spreads by inheritance rather than by
  instruction.

**Say it like this.** "The adaptation point matters more than it sounds. Every organisation wants
the agents to follow its own templates and terminology. The wrong answers are forking the product
per client, or letting people edit prompts globally. Here a builder changes an agent in their own
sandbox, and it becomes the team's default only when the owner of that scope publishes it. Your
standards become configuration with a trail — not a support ticket to us."

**Answers:** *Will it work inside my estate, with my tools and my standards — and how hard is it to change?*

---

## Slide 10 — The decision

> ### Pick one track, two workstreams and a baseline. Ninety days tells you the truth.

**On screen**

| | |
|---|---|
| **Days 0–15** | Deploy into your estate — one VM. Connect one board, one repository, one wiki. Stand up one Business Unit and one project |
| **Days 15–30** | **Agree the baseline from your own data**: demand-to-PR lead time, PR-to-release lead time, rework and escaped defects, and today's cost per release |
| **Days 30–60** | Run two real workstreams end to end on **Track 1 or Track 2** — real code, real board, and **your** approvers standing in the gates |
| **Days 60–90** | Measure against the baseline. Walk the audit trail and the cost rollup with Risk and Finance. Decide the rollout scope |

**The five measures we will report against** — agreed up front, and no others:
1. Demand → PR lead time
2. PR → release lead time
3. Escaped defects and rework
4. Policy violations and high-risk findings caught **before** production
5. Cost per workstream against budget

**What we need from you:** an executive sponsor, one Business Unit, two real workstreams, named
approvers, and agreement on those five measures.

**What you get either way:** an evidenced answer about AI in your delivery lifecycle — produced on
your systems, with your data, under your approvals. Not a vendor's number.

**Say it like this.** "I haven't given you a productivity percentage today and I'm not going to —
the product's own scope statement forbids claims without a client-specific baseline we both agree
on. What I'm proposing instead is ninety days that produce your number. If it isn't good enough,
you'll have found that out on two workstreams rather than across a programme. One Business Unit,
two workstreams, five measures. Who's the sponsor?"

**Answers:** *What do I decide today, and how will I know in ninety days whether I was right?*

---

## Appendix — backup slides to have loaded, not shown

Hold these behind slide 10 and bring one up only when asked.

| Backup | Bring it up when | Source |
|---|---|---|
| **The twelve-minute demo video**, or any single clip | Anyone wants to see it rather than hear it | `Demo_Video_Script.docx` — fifteen clips, in flow order |
| **A live cost rollup and a live trace** | Anyone doubts slide 8 | The running platform — ⟨Cost⟩ and ⟨Traces⟩ pages |
| **A real audit-trail export** | Anyone doubts slide 7 | The running platform — ⟨Audit Trail⟩ page |
| **The test-case workbooks and a run report** | A QA or delivery lead wants the detail | The demo project's Documents panel |
| **Role × permission matrix** | The CISO or an IAM lead asks who can do what | PRD §14.10–14.11 |
| **Non-functional requirements and the threat model** | Procurement or a security deep-dive | PRD §17–18 |
| **Deployment architecture and dependency inventory** | Infrastructure asks what lands on the VM and what it must reach | `docs/platform-overview.md`, `docs/dependencies.md` |
| **Deployer's FAQ** — Docker, one machine, ports, backups, upgrades | The person who will install it is in the room | `docs/deployment-faq.md` |

**Never improvise in these areas.** Unattended autonomy, a productivity percentage, a certification
the platform does not hold, a connector not listed on slide 9, or an agent from the right-hand
column of the claims ledger. The honest answer — "not today; here is what exists now" — is what has
been winning these rooms.
