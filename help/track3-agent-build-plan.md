# Track 3 (Code Modernization) — what actually has to be built

Companion to `help/multi-track-agent-access-design.md` §Portfolio 2 (lines 358–376),
which defines the 10-agent shape but doesn't say which of those 10 can reuse Track 1
code. This doc answers that question directly, grounded in what's actually in the
repo today: `backend/config/agent_registry.py` has 9 entries, all Track 1
(`requirements, design, plan, development, code_review, security, testing,
deployment, documentation`), and `TRACK_PORTFOLIOS["modernization"] = []` — Track 3
has zero agents built, zero mounted routers, nothing to extend.

## The core problem with the shared names

Six of Track 3's ten agents reuse a Track 1 name and owning role (Design, Development,
Code Review, Security, Testing, Deployment, Documentation minus Requirements —
actually seven). §1.4 of the design doc is explicit that this is *not* the same case
as Track 2, which genuinely reuses Track 1's agents unchanged: "no agent here is a
reference to or reuse of a Greenfield/Brownfield agent, even where one shares a name
and owning role." The reason is structural, not cosmetic — every Track 1 agent's
`required_capabilities` list in `agent_registry.py` is built around a single premise
that Track 3 breaks: **there is one repo, and it starts empty or gets extended.**
Track 3 always has *two* repos in play (legacy source, target output), a behavior
that must be *proven equivalent* rather than *tested against requirements*, and a
release that's a multi-week phased cutover rather than a single deploy. That premise
break is why a same-named Track 3 agent can't just be Track 1's agent pointed at
different input — the capability list itself has to change.

## Verdict per agent

| # | Agent | Track 1 analog? | Verdict | Why |
|---|---|---|---|---|
| 1 | Requirements (migration-intent) | `requirements` (BA) | **Rebuild** | Track 1 generates BRD/user-stories/Gherkin ACs for new functionality. Track 3 captures scope/constraints against a system that already exists and produces a migration-intent brief, not a story backlog. Different output schema, different prompt, most of the generation logic doesn't apply. |
| 2 | Discovery & Assessment | none | **Net new** | No Track 1 agent reads an external legacy repo, builds a dependency graph, or scores modules for migration risk. Closest thing is Design's *optional* `design.system.analyze`, which is nowhere near this scope. |
| 3 | Design | `design` (Architect) | **Rebuild** | Track 1 Design turns requirements into HLD/LLD/ADRs for something not yet built. Track 3 Design has to choose a *migration pattern* (rewrite vs. strangler-fig vs. branch-by-abstraction) and a target stack from an assessment — a decision space Track 1 Design has no vocabulary for. Diagram-rendering and ADR-writing plumbing carries over; the decision logic doesn't. |
| 4 | Strategy | none (closest: `plan`) | **Net new** | Plan schedules delivery of new work items against sprint capacity. Strategy sequences *migration* of existing modules by risk, with per-module equivalence criteria and a legacy-side change-freeze policy. Different unit of work entirely — may reuse Plan's board-write/capacity-read plumbing, nothing else. |
| 5 | Development | `development` (Developer/Architect) | **Rebuild** | Track 1 Development edits one repo. Track 3 Development reads a legacy repo, invokes codemod/upgrade tooling (language-specific — e.g. `dotnet upgrade-assistant`/`try-convert` for same-language .NET upgrades, OpenRewrite/Windup for Java-family) or LLM-assisted rewriting where no tool exists, migrates the *build system* as well as source, and must preserve external behavior by construction. The vcs/branch/PR plumbing carries over; everything about what it does to code does not. |
| 6 | Code Review | `code_review` (Architect) | **Rebuild** | Track 1 reviews a diff against requirements coverage. Track 3 reviews a diff against *target design and equivalence criteria* and must flag legacy anti-patterns carried over verbatim — a different traceability model, not a parameter change. |
| 7 | Security | `security` (Security Engineer) | **Extend, don't rebuild** | Smallest delta of the ten. Same scan stack (SCA/SAST/secrets/SBOM/signoff) plus one addition: scanning for insecure patterns copied verbatim from the legacy code, which needs dual-repo scan input. Needs its own `AgentDefinition`/router (inputs differ), but ~90% of the capability list is identical to Track 1's. |
| 8 | Testing | `testing` (QA/Tester) | **Rebuild** | Track 1 tests one system against a test plan. Track 3 runs *differential/equivalence* testing — executing legacy and modernized code against identical inputs and diffing the outputs, plus a performance-regression comparison (cross-runtime moves change GC/threading/I/O behavior even when functional output matches). This requires provisioning a sandboxed legacy runtime alongside the new one, something Track 1 Testing has no concept of. |
| 9 | Deployment | `deployment` (DevOps Engineer) | **Rebuild** | Track 1 deploys once, with rollback as a fallback plan. Track 3 manages a *phased cutover* with a parallel-run window and traffic-shifting before legacy is switched off, then schedules legacy decommission. Readiness-assessment and gate-aggregation concepts carry over; the release model itself doesn't. |
| 10 | Documentation | `documentation` (BA/PA) | **Rebuild (light)** | Track 1 produces a first-release doc set. Track 3 produces a cutover pack: updated SDD, an old-to-new traceability map, equivalence evidence, and a decommission note — document types Track 1 has no generator for. repo-read/doc-generate/PR-create plumbing carries over; the content generation doesn't. |

**Net result: of the 10 agents, 2 are entirely net-new, 7 need a full rebuild despite
sharing a name and owning role, and only 1 (Security) is a genuine extension of its
Track 1 counterpart.** Zero are drop-in reuse. This confirms the suspicion — even the
seven that look identical on the portfolio table are separate `AgentDefinition`
entries with separate capability lists, separate prompts, and separate implementations
under `backend/agents_orchestrator/`, the same way Track 2 is the one legitimate
exception and Track 3 explicitly is not (§1.4).

## New capability tokens this implies

Following the existing dotted-namespace convention in `agent_registry.py`
(`design.hld.generate`, `deploy.rollback.plan`, etc.), Track 3 needs roughly:

- **Discovery & Assessment**: `discovery.repo.clone`, `discovery.dependency.graph.build`, `discovery.dependency.eol.scan`, `discovery.dependency.cve.scan`, `discovery.module.risk.score`, `discovery.module.tier.classify` (mechanical / LLM-assisted / manual-only), `discovery.golden_master.capture` (behavior baseline snapshot — must happen here, before anything else touches the legacy system, or Testing has nothing to diff against later)
- **Strategy**: `migration.sequence.plan`, `migration.wave.define`, `migration.equivalence.criteria.define`, `migration.freeze.policy.define`
- **Design**: promote `design.tech.stack.recommend` from optional to required; add `design.migration.pattern.select`, `design.legacy.interop.plan`
- **Development**: `code.migrate.tooling.invoke`, `code.buildsystem.migrate`, `code.behavior.preserve.verify`
- **Code Review**: `review.equivalence.criteria.check`, `review.legacy.antipattern.detect`
- **Security**: `sec.legacy.pattern.scan` (the one addition to the existing list)
- **Testing**: `test.differential.run`, `test.golden_master.diff`, `test.perf.regression.compare`, `test.legacy.sandbox.provision`
- **Deployment**: `deploy.cutover.phase.plan`, `deploy.parallel_run.manage`, `deploy.traffic.shift`, `deploy.legacy.decommission.schedule`
- **Documentation**: `doc.traceability.map.generate`, `doc.decommission.note.generate`, `doc.equivalence.evidence.compile`

## Build order

`AgentDefinition`'s `input_artifacts` chain fixes most of the order, same as Track 1.
One deliberate deviation: build and validate **Discovery & Assessment before wiring
Requirements**, even though Requirements sits first in the pipeline. It's the highest-
leverage net-new agent — golden-master capture and risk-tiering are load-bearing for
Design, Strategy, and Testing alike — and it's the one piece of Track 3 with no Track 1
scaffolding to lean on at all, so it carries the most implementation risk. Validate it
against a real legacy repo before investing in the other nine.

1. Discovery & Assessment (spike/validate standalone first)
2. Requirements (migration-intent)
3. Design
4. Strategy
5. Development
6. Code Review + Security (parallel, same as Track 1's `can_parallel_with`)
7. Testing
8. Deployment
9. Documentation

Each agent, once built, gets its own `AgentDefinition` entry in `agent_registry.py`
and is added to `TRACK_PORTFOLIOS["modernization"]` only at that point — per the
existing comment there, an agent id is added "only once it's actually built and
mounted," not as a placeholder ahead of time.
