# Capability deck — CXO edition

**The client-facing deck.** Ten slides for a CIO, CTO, CISO, CFO or Head of Delivery, in any
industry. Every capability on these slides is built and has run on a real project — a real
repository, a real Azure DevOps board, a real Confluence space. Nothing here is roadmap.

**Its sibling.** `docs/cxo-capability-deck.md` is the internal edition: the same ten slides plus a
claims ledger of what is built against what is only designed, and the known rough edges. **Read
that one before you present this one.** If a question goes outside what is on these slides, the
answer lives there — and the honest answer, "not today; here is what exists now", is the one that
wins these rooms.

**Running time.** 20–25 minutes, 15 for questions. Slides 5, 6 and 8 win the room. Cut to ten
minutes: 1, 2, 3, 5 and 10.

**Two rules.**

1. **⟨Angle brackets⟩ are blanks you fill in for the client** — their tools, their estate, their
   numbers. Never present one as delivered.
2. **No productivity percentage, anywhere.** Slide 10 turns that into the close: we don't quote a
   number, we produce theirs. In a market full of unfalsifiable claims, this is the most credible
   thing in the deck.

**Swapping the industry.** Only slide 4 carries industry examples. Replace that column and the deck
travels from a bank to a hospital group to a manufacturer without another edit.

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

**Say it like this.** "I'm not here to show you another code-generation assistant — you have those.
This is the layer above them: what the agents are allowed to do, who signs off, what evidence
survives, and what it costs. Without that layer, AI in delivery stays a pilot forever. And
everything I show you today comes from a run that actually happened, on a real repository and a
real Azure DevOps board."

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

## Slide 3 — What it is

> ### One project record, an agent for every stage, and a person at every gate.

**On screen — four facts that hold everywhere in the product**

| | |
|---|---|
| **1. An agent for each stage of delivery** | Requirements, Design, Project Manager, Development, Code Review, Testing, Security, Deployment, Documentation — plus an Orchestrator that takes a request in plain language and routes it to whichever agent owns it |
| **2. Everything an agent produces is a document** | Raised for approval by whoever produced it, decided by a named approver, recorded against the project. One queue to decide in; one trail of who approved what, and when |
| **3. Each stage consumes what the last one had approved** | The architecture is written from the *approved* requirements; the code from the *approved* architecture; the handover from all of it. Drafts do not propagate |
| **4. Access reaches down to the individual agent** | A project admin onboards each role and grants the specific agents that person may open. The Business Unit approves the project itself before any work starts |

**And what it deliberately is not**

- Not a replacement for your systems of record — your repository, board, wiki and pipelines stay
  exactly where they are.
- Nothing runs unattended: the agent proposes, a person approves, and only then does the action
  reach your systems.
- No productivity claim without your baseline.

**Say it like this.** "Four facts, and the second is the one to hold on to. Every output is a
document with an approver's name on it, and the next agent only reads what was approved. That is
what makes the trail at the end worth anything — and it's the difference between a demo and an
audit."

**Answers:** *What am I actually buying, and what does it leave alone?*

---

## Slide 4 — Coverage

> ### The three kinds of demand every engineering portfolio runs on.

**On screen**

| The demand | What the platform does with it | Where it lands |
|---|---|---|
| **Build something new** | The full nine-agent run: discovery call to handover, every artifact approved on the way | ⟨A customer-onboarding journey in BFSI · a patient portal in healthcare · a D2C storefront in retail⟩ |
| **Change something that exists** | The same nine agents, entered at whichever stage the change actually needs — a defect goes straight to development and test; a feature starts at requirements | ⟨Peak-season defect flow · claims fixes · plant-floor change requests⟩ — the permanent majority of any portfolio |
| **Decide what to do with a legacy estate** | *Migration Intent* captures why the modernization is happening, from what to what, and what success looks like. *Dependency & Risk* clones the legacy repository read-only, maps its dependencies, flags what is past end of life, and scores each module's risk — so the business case is evidence, not opinion | ⟨Core banking · policy administration · public-sector legacy estates⟩ |

**Under the table:** *one control plane, one set of roles, one approval trail, one cost model. New
kinds of delivery arrive as configuration on the same platform — a template, not another product,
and not another procurement.*

**Say it like this.** "Most organisations buy separately for these three, each with its own access
model and its own blind spots. The third one is where I'd start a conversation in ⟨sector⟩: before
anyone commits to a modernization programme, two agents will tell you what is actually in that
estate and what it will cost you to leave it alone."

**Answers:** *Does this cover my real portfolio, or only new builds?*

---

## Slide 5 — The proof

> ### Discovery call to handover — one real project, not a montage.

**On screen** — the thread, left to right, with what each stage produced:

| Stage | Owner | What it did on the real run |
|---|---|---|
| **Requirements** | BA | Turned the recorded discovery call into a BRD in the house template; after approval, published it to Confluence and wrote the epics and user stories straight onto the Azure DevOps board, hierarchy intact |
| **Design** | Architect | Wrote the architecture from the approved BRD, inside the technology stack the organisation allows — high-level and low-level design, API contracts, database schema, and the decisions behind them |
| **Project Manager** | Project admin | Turned the design into a schedule — work breakdown, estimates, sprints, who is on what — built from what is approved, not from a wish list |
| **Development** | Developer | Built the codebase from the approved documents, pushed it to a branch in Azure DevOps, then read the board and picked up the stories assigned to it |
| **Code Review** | Architect | Read **every file on the branch** — 14 reviewable files, 325 lines — mapped the code back to the approved requirements and architecture, ran secret, static and dependency scans, and returned findings citing file and line |
| **Testing** | QA | Wrote **unit, functional and API** cases in one pass, each an Excel workbook read and **approved before anything ran**. Unit tests executed in the repository. Functional cases drove the running application **in a browser**, step by step, keeping a screenshot on failure. API cases called every endpoint in order, passing values from one case into the next |
| **Security** | Security engineer | Scanned the branch for secrets, code weaknesses and vulnerable dependencies: 13 vulnerabilities, 9 of them high or critical, signed off with the risk recorded as accepted |
| **Deployment** | DevOps | Staged the release — container image, manifests, pipeline, runbooks — and returned a decision: **conditional go** |
| **Documentation** | Architect | Compiled the handover from everything upstream: scope, access, work in flight, known risks — and where something is undocumented, it says so and names who to ask |

> **The moment to hold on:** a generated functional case found a **real defect** — the create page
> never showed its validation error. Nobody wrote that test.

**Three refusals worth naming out loud**

- The security agent **cannot quietly pass a critical finding**; a sign-off that leaves one out is
  refused.
- The deployment agent marks what it did not measure **unknown** — unknown is never counted as
  passed.
- Approving the deployment report **deploys nothing**. The decision and the action stay separate.

**Say it like this.** "One project, one thread, and notice where it was unflattering to itself:
nine findings were high or critical and the sign-off recorded them rather than burying them, and a
test we didn't write found a bug we didn't know about. An agent that only ever agrees with you is
not an assurance system."

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

**What that looks like day to day**

- Approval is the **same everywhere**: raised by whoever produced the work, from the chat or from
  the document; the approver sees what was raised and by whom; the approved document becomes the
  project's record and carries "Approved by".
- **One queue** to decide in, **one trail** of who approved what and when, and a history that keeps
  every generation and every run — who ran it, and against which branch.
- **Access reaches the individual agent.** Two people in the same role need not have the same
  agents. The Business Unit approves the project before any work starts.
- **Human-directed by design.** The agent proposes; a person decides; the action then runs. That is
  the design, not a setting someone can turn off in a hurry.

**Say it like this.** "This is the slide your CISO came for. Every consequential step stopped in
front of a person who could have said no — and the record proves which one of them did. That is
what lets you say yes to agents in a regulated estate."

**Answers:** *What can the AI do without a human, and who is accountable when it's wrong?*

---

## Slide 7 — Control, isolation and audit

> ### Access that matches your org chart, and a record that outlives the project.

**On screen**

- **Three levels, granted downward.** Organization → Business Unit → Project, and no administrator
  can grant a permission they do not themselves hold.
- **The Business Unit is the blast radius.** Its own budget, its own connected ⟨Azure DevOps /
  Jira⟩ instance, no visibility into the others. Two separate Jira sites are two Business Units. A
  group that must wall off subsidiaries can run isolated Organizations in one install.
- **Governance roles govern; delivery roles deliver.** Organization and Business Unit Admins set
  structure, budget and policy — and hold no agent access at all.
- **Tools are granted top-down; identity is supplied bottom-up.** The unit registers the connector;
  each person links **their own** account; the agent acts as that person, with that person's
  permissions, in your systems. There is no shared production token.
- **Isolation is enforced in the database**, by row-level security on every tenant-scoped table —
  not only in application code.
- **An audit trail** of access, approvals, runs, role changes and configuration changes, each with
  actor, timestamp and scope, append-only by database privilege rather than by application
  convention.

**Say it like this.** "Two details worth pausing on. The agent never gets its own super-account in
your estate — it acts as the person driving it, so your existing tool-side permissions still apply.
And isolation is enforced by the database itself, which means a defect in our application code
cannot quietly leak one client's project into another's."

**Answers:** *Can I give this to a regulator, and what is the blast radius if something goes wrong?*

---

## Slide 8 — The economics

> ### Every model call is priced and attributed — and the cap is enforced, not advisory.

**On screen**

- **Every model call is logged** with its tokens and its cost, per project, per agent, per model.
  The Cost page answers "what did this stage cost" as a fact, not an estimate.
- **Budgets nest, and the top tier wins.** A Business Unit cannot exceed the organization's cap for
  it; a project cannot exceed its unit's.
- **The cap is enforced fail-closed** — checked before a run starts, and again every time an agent
  reaches for a model. An exhausted scope stays blocked until a person raises the figure. It is not
  a chart you read after the money is gone.
- **Your models, your keys.** Every agent page carries a model picker, so you choose which model
  each stage runs on. Keys are held per organization, encrypted, in your database — never in a
  configuration file, never ours. Anthropic, OpenAI, Google, xAI and others through one interface,
  so changing provider is configuration, not a project.
- **Call-by-call tracing, self-hosted** inside your estate, with no third-party analytics in the
  path.

> **And the discipline:** we do not quote you a productivity percentage. We instrument the run so
> that **your** baseline produces **your** number — slide 10.

**Say it like this.** "Three questions get asked in every one of these meetings: what does it cost,
where does the money go, and what stops it running away. Most products answer the third with a
dashboard. Here the budget check runs before the agent starts and again every time it reaches for a
model, and it fails closed."

**Answers:** *What will this cost me, and how do I stop it running away?*

---

## Slide 9 — Fits your estate

> ### Deployed in your estate, wired to your tools, tuned to your standards — without a fork.

**On screen — three columns**

**Deployed where you decide**
- Client-deployed and single-tenant. One Linux VM runs the whole platform: application, database,
  cache and a web front door.
- **Docker is not required** anywhere in the platform.
- Only **443** is exposed; everything else listens behind the front door.
- Documents live on your disk or in your ⟨Azure Blob⟩ tenancy. Your code and documents do not leave
  your boundary except to the model provider you approved.

**Wired to what you already run**
- **Azure DevOps** — boards, repositories and pull requests — and **Confluence** and **SharePoint**
  for the documents the rest of the organisation reads.
- Also connected: Azure Pipelines, GitHub Issues and Actions, Jira, Microsoft Teams, Slack,
  SonarQube, Figma.
- Bring your own tools over MCP, assessed and logged like any other component.

**Tuned to how you work — Agent Studio**
- The behaviour instructions and reusable skills behind each agent, scoped **organization →
  Business Unit → project → a personal sandbox**, resolved most-specific-first.
- A builder tunes an agent in their **own sandbox**, where it affects only their own runs, then
  proposes it to the owner of that scope to publish. Versioned, checked before it goes live, and
  rolled back by publishing an earlier version.
- New projects start from the unit's defaults, so a standard spreads by inheritance rather than by
  instruction.

**Say it like this.** "The adaptation point matters more than it sounds. Every organisation wants
the agents to follow its own templates and terminology. The wrong answers are forking the product
per client, or letting people edit prompts globally and hope. Here a builder changes an agent in
their own sandbox, and it becomes the team's default only when the owner of that scope publishes
it. Your standards become configuration with a trail — not a support ticket to us."

**Answers:** *Will it work inside my estate, with my tools and my standards — and how hard is it to change?*

---

## Slide 10 — The decision

> ### Pick two workstreams and a baseline. Ninety days tells you the truth.

**On screen**

| | |
|---|---|
| **Days 0–15** | Deploy into your estate — one VM. Connect one board, one repository, one wiki. Stand up one Business Unit and one project |
| **Days 15–30** | **Agree the baseline from your own data**: demand-to-PR lead time, PR-to-release lead time, rework and escaped defects, and today's cost per release |
| **Days 30–60** | Run two real workstreams end to end — real code, real board, and **your** approvers standing in the gates |
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

**Say it like this.** "I haven't given you a productivity percentage today and I'm not going to.
What I'm proposing instead is ninety days that produce your number. If it isn't good enough, you'll
have found that out on two workstreams rather than across a programme. One Business Unit, two
workstreams, five measures. Who's the sponsor?"

**Answers:** *What do I decide today, and how will I know in ninety days whether I was right?*

---

## Appendix — backup slides to have loaded, not shown

Hold these behind slide 10 and bring one up only when asked.

| Backup | Bring it up when |
|---|---|
| **The twelve-minute demo video**, or any single clip from it | Anyone wants to see it rather than hear it |
| **A live cost rollup and a live trace** | Anyone doubts slide 8 |
| **A real audit-trail export** | Anyone doubts slide 7 |
| **The test-case workbooks and a run report** | A QA or delivery lead wants the detail behind slide 5 |
| **Role × permission matrix** | The CISO or an identity lead asks precisely who can do what |
| **Non-functional requirements and the threat model** | Procurement, or a security deep-dive |
| **Deployment architecture and dependency inventory** | Infrastructure asks what lands on the VM and what it must reach |
| **Deployer's FAQ** — one machine, ports, backups, upgrades | The person who will install it is in the room |

**One standing instruction.** Everything on these ten slides is built and demonstrable. If a
question reaches past them, do not improvise an answer — check the internal edition
(`docs/cxo-capability-deck.md`) and say what exists today. That sentence has closed more of these
rooms than any claim in the deck.
