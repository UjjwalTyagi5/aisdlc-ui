# Agent Studio Sub-Project 4 — Evaluation-Gated Promotion

**Status:** design, self-reviewed, ready for planning.

## 1. Problem statement

Sub-project 3 gave Behavior and Skills a real `propose()` -> governance-approval
workflow: a non-owner drafts a change, the tier's owner approves it, approving flips
the draft live. Nothing in that pipeline checks whether the draft is actually GOOD —
an approver can only read the raw prompt/skill text and decide on judgment alone, with
no structured signal, no record of anyone having run it against a fixed set of
expectations, and no distinction between "a low-stakes project tweak" and "an
org-wide prompt change every workspace inherits."

The master ledger's confirmed gap: "Zero evaluation-gating infrastructure anywhere in
the codebase (no Evaluation model, no PASS-gate logic in `governance_requests.py`)."
This sub-project closes it: every proposal must carry a PASSING evaluation against a
fixed, per-agent rubric before it can be filed, and for org-scope proposals (the
broadest blast radius — every workspace and project in the tenant inherits from it)
that evaluation must have been run by someone other than the person who wrote the
draft.

## 2. What already exists (survey, do not rebuild)

- **`shared/eval/scoring.py`** (`EvalSignals`, `score_output()`) — a DETERMINISTIC,
  OFFLINE, NO-LLM scorer (`D-M9-03`/`REQ-M9-13` forbid an LLM call specifically so CI
  can run it with no network) comparing an agent RUN's output ARTIFACT text against an
  `expected` value. Two strategies: required-section presence for `design_architecture`
  (7 fixed headers), token-overlap Jaccard for everything else. **Not reusable
  as-is**: it answers "does this design DOCUMENT contain its 7 sections", not "is this
  PROMPT/SKILL DRAFT any good" — there is no `expected` text for a system-prompt draft
  to diff against (the draft itself is the new content, not a document being graded
  against a known-good copy).
- **`shared/eval/service.py`** (`EvalRecordService`/`eval_service` singleton,
  `EvalRecord` ORM model, `eval_records` table) — fire-and-forget, best-effort
  TELEMETRY for one agent RUN's output quality (`run_id`, `agent_type`, `score`,
  `signals`), written via `asyncio.create_task` from `workflows/activities/_base.py`.
  **Not reusable as the gate's store**: it has no PASS/FAIL concept, no linkage to a
  governance request or a specific `AgentProfile`/`AgentSkill` draft VERSION, no
  evaluator identity, and is explicitly "not an audit/compliance record" (no
  dead-letter retry, silently drops on failure) — the opposite of what a promotion
  gate needs (a durable, queryable PASS record `decide()` can trust).
- **`frontend/lib/catalogue.ts`** `RISK_TIERS` — PRD §13's static R1-R4 reference text
  (Internal advisory / Internal delivery / Customer-impacting / Regulated), rendered
  today only on a docs/reference page. No code anywhere classifies an actual resource
  (a project, an agent action, an Agent Studio draft) into one of these tiers — it is
  pure copy, not a working mechanism.
- **`FORBIDDEN_PATTERNS`** (`agent_profiles.py`, shared with `agent_skills.py`) —
  the existing prompt-injection deny-regex list, already run against every
  draft/skill body at write time (lint). Reusable directly as one PASS/FAIL input.
- **`governance_requests.decide()`** — single choke point (module docstring: "THE
  RULES LIVE HERE, NOT IN THE CONTROLLER... reachable from more than one [path]"),
  already has a numbered rule sequence (self-approval / still-open / current-approver
  / effect-can-apply). A new gate slots in as an additional numbered rule, following
  the same pattern rule 4 already established for "an approval that cannot take
  effect is refused, not recorded."
- **Confirmed gap, disclosed in sub-project 2's ledger**: a personal-tier draft has
  no effect at actual agent-run time yet (`resolve_profile`/`resolve_active_skills`
  don't handle `scope="user"`). More broadly, nothing in this codebase can invoke a
  live agent turn against an arbitrary CANDIDATE draft (org/workspace/project or
  personal) for a real "run it and see" evaluation — that capability does not exist
  as a prerequisite. **This sub-project does not build it.** A "golden task" here
  is therefore a deterministic, offline TEXT-level rubric check against the draft's
  own content (mirroring `score_output`'s own architecture, not a live LLM
  invocation) — consistent with `D-M9-03`'s existing no-LLM-in-eval precedent, and
  buildable without first solving preview-mode agent execution.

## 3. Scope

### 3.1 Golden-task rubric (new, deterministic, offline)

`shared/eval/agent_studio_scoring.py` (new module, sibling to `scoring.py`, same
architecture — no LLM call, pure string operations):

- `AGENT_REQUIRED_TOPICS: dict[str, tuple[str, ...]]` — per pipeline `agent_id`
  (the 8 keys already in `AGENT_REGISTRY`), a short tuple of lowercase keyword/phrase
  fragments the draft body should mention at least one of, per topic — mirrors
  `DESIGN_REQUIRED_SECTIONS`' exact shape (a fixed tuple checked for presence), scaled
  down to keyword-fragment granularity since a prompt/skill body has no fixed section
  headers to check for. Content is genuinely per-agent (e.g. `requirements`:
  ("acceptance criteria", "stakeholder", "scope"); `security`: ("vulnerability",
  "owasp", "threat")) — authored once, reviewed as part of the plan's own PR, not
  invented per draft.
- `score_agent_default(agent_id: str, body: str) -> EvalSignals` — reuses the
  existing `EvalSignals` model from `scoring.py` (score + signals dict, same shape
  downstream code already expects). Computes:
  - `topic_hits` = how many of `AGENT_REQUIRED_TOPICS[agent_id]`'s keyword-groups
    appear (case-insensitive substring) in `body`, as a ratio (mirrors
    `_score_design_sections`'s `present/total` shape exactly).
  - `forbidden_hits` = `FORBIDDEN_PATTERNS` matches found in `body` (imported from
    `agent_profiles.py`, already the canonical list both Behavior and Skills lint
    against at write time — this is a SECOND check at evaluation time, not a
    duplicate of the write-time lint: the lint blocks obviously-malicious content
    outright; this scoring pass folds the same signal into the PASS/FAIL score so a
    borderline draft that slipped past lint with a partial match still shows up in
    the evaluation record).
  - `score` = `topic_hits_ratio`, clamped to `[0.0, 1.0]` — same clamp contract as
    `score_output`.
  - `signals` = `{"topics_present": [...], "topics_missing": [...],
    "forbidden_hits": [...]}`.
- `PASS_THRESHOLD = 0.5` (module constant, `agent_studio_scoring.py`) — at least half
  of an agent's required topics present. **Fails outright regardless of score** if
  `forbidden_hits` is non-empty (a forbidden-pattern hit is disqualifying on its own,
  independent of topic coverage — mirrors the existing lint's own all-or-nothing
  behavior for the same list).
- `evaluate_agent_default(agent_id: str, body: str) -> tuple[bool, EvalSignals]` —
  `(result_is_pass, signals)`, the one function the router calls. `result_is_pass =
  score >= PASS_THRESHOLD and not signals["forbidden_hits"]`.

### 3.2 Store: `agent_default_evaluations` (new table + service)

New ORM model `AgentDefaultEvaluation` (`shared/models/orm.py`, tenant-scoped under
FORCE RLS, mirroring `AgentSkill`'s existing shape):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `tenant_id` | UUID | RLS key |
| `target_type` | String(16) | `'profile'` \| `'skill'` — mirrors `effects.py`'s existing dual-resolution convention for `target_ref` (an `AgentProfile` id vs an `AgentSkill` id) rather than inventing a third convention |
| `target_id` | UUID | the `AgentProfile.id` or `AgentSkill.id` this evaluation was run against — ONE SPECIFIC VERSION, never "the skill_key in general" (a later draft of the same key needs its own evaluation) |
| `agent_id` | String(50) | denormalized for querying without a join, matches `AgentProfile.agent_id`/`AgentSkill.agent_id` |
| `scope` | String(16) | denormalized, `org`\|`workspace`\|`project` — `user` is never evaluated (see §3.4) |
| `result` | String(8) | `'pass'` \| `'fail'` |
| `score` | Float | from `EvalSignals.score` |
| `signals` | JSONB | from `EvalSignals.signals` |
| `evaluator_id` | String(255) | who ran it — `_user_id(request)`, same convention as `AgentSkill.created_by` |
| `evaluator_role` | String \| NULL | for audit display, same pattern as governance's `initiator_role` |
| `created_at` | DateTime(tz) | server_default now() |

No `updated_at`/mutation — evaluations are append-only (same immutability rationale
`governance_requests.py`'s module docstring already states for
`governance_request_events`: a history nothing should be able to tidy up after the
fact).

`shared/services/eval_gate.py` (new, small, single-purpose — NOT added to
`shared/eval/service.py`, which is a different fire-and-forget telemetry contract
this deliberately does not share, per §2's "not reusable" finding):

- `run_evaluation(tenant_id, target_type, target_id, agent_id, scope, body,
  evaluator_id, evaluator_role) -> dict` — calls
  `evaluate_agent_default(agent_id, body)`, inserts one `AgentDefaultEvaluation` row,
  returns it as a dict (mirrors `skill_store.py`'s `_list_item`-style plain-dict
  return convention other routers already consume).
- `latest_passing_evaluation(tenant_id, target_type, target_id) -> Optional[dict]` —
  the newest row for this exact `target_id` with `result='pass'`, or `None`. This is
  what the gate checks — "does a PASS exist for exactly this version", not "did this
  skill_key ever pass at some prior version."

### 3.3 The gate

Two checkpoints, both required (defense in depth, matching this codebase's own
established pattern of a route-level check plus a service-level re-check — see
sub-project 3's `assert_can_write_agent_scope` wrapping `resolve_actor_tier_access`):

1. **At `propose()` / `propose_skill()`** (the earliest point a target VERSION is
   fixed): before filing the governance request, require
   `latest_passing_evaluation(tenant_id, target_type, target.id)` to be non-None.
   Absent -> `422 {"code": "EVALUATION_REQUIRED", "message": "Run an evaluation
   before proposing this change."}`. This stops a doomed request from ever reaching
   an approver's queue — the same "refuse early rather than let it dead-end"
   philosophy `governance_requests.py`'s own module docstring states for rule 3
   (escalation ceiling).
2. **At `governance_requests.decide()`**, approve path, for `request["type"] in
   ("agent_default_org", "agent_default_workspace", "agent_default_project")`
   specifically (not every governance type — evaluation is an Agent-Studio-specific
   concept, the same scoping `effects.py`'s `_apply_agent_default` dispatch already
   uses): re-verify a passing evaluation still exists for `request["targetRef"]`
   before calling `apply_on_approve`. This is the belt to `propose()`'s suspenders —
   closes the gap where a request was filed validly, but something invalidated its
   evaluation before an approver acted (see §3.5). Refusal here reuses the EXISTING
   `EffectUnavailable`/422 shape `decide()` already raises for rule 4
   ("an approval that cannot take effect is refused, not recorded") — no new
   exception type needed, since this IS that rule, just checked one line earlier.

**Owner's direct publish/activate path is NOT gated.** `publish()` (Behavior) and
`activate_version` (Skills) are the OWNER acting on their own tier directly — no
governance request is filed at all for that path (unchanged from today). Gating it
would mean an org_admin cannot publish their own org-tier draft without first
evaluating it, which is a real, defensible product decision but a LARGER one than
this sub-project's stated scope ("gate wired into `governance_requests.decide()`") —
out of scope here, flagged as a natural follow-up in §6.

### 3.4 Risk-tier mapping and self-evaluation-blocked

No code anywhere maps an `AgentProfile`/`AgentSkill` scope to a PRD R1-R4 risk tier
(§2) — this sub-project must define one to implement "R3/R4 self-evaluation-blocked,
R1/R2 may self-evaluate." Mapping, by blast radius (the same reasoning
`resolve_actor_tier_access`'s own docstring already uses to justify per-tier
ownership rules):

| Scope | Blast radius | Risk tier | Self-evaluation |
|---|---|---|---|
| `org` | every workspace + project in the tenant inherits | **R3** | BLOCKED — evaluator must not be the draft's own `created_by` |
| `workspace` | one business unit and its projects | R2 | allowed |
| `project` | one project | R2 | allowed |
| `user` | the author alone | R1 | N/A — personal drafts are never proposed (existing rule, unchanged), so never evaluated at all |

R4 ("regulated / high-consequence... specialist approval quorum") has no mapping:
nothing in Agent Studio's config model carries a "this touches regulated data" flag,
and inventing one is a materially different, larger feature (data-classification
tagging) this sub-project's own PRD reference (§27-31) does not ask for here — R4 is
therefore out of scope, not silently mis-mapped onto `org`.

Enforcement: `run_evaluation`'s caller (the route) rejects with `403
{"code":"SELF_EVALUATION_BLOCKED", ...}` when `scope == "org"` AND
`evaluator_id == target.created_by` (Behavior) / `== row.created_by` (Skills) — the
same "self-X is blocked" shape `governance_requests.py` already uses for
self-approval (400, not 403, there — evaluation reuses 403 since, unlike
self-approval, a DIFFERENT actor entirely is permitted to run this same action; the
combination that is wrong is narrower: only org-scope AND the same person).

### 3.5 What invalidates an evaluation

A later edit to the SAME draft version cannot happen — `AgentProfile`/`AgentSkill`
versions are immutable once inserted (a new edit always inserts a NEW version row,
per both routers' existing `create_draft`/`update_custom_skill` behavior). An
evaluation is therefore permanently valid for the exact `target_id` it was run
against; there is no "the draft changed under the evaluation" race to handle. The
only way a stored PASS stops being useful is the draft it targets getting superseded
by ANOTHER edit (a new version, with its own `target_id`, needing its own
evaluation) — `propose()` already resolves the LATEST draft via its own existing
logic, so an evaluation against a stale, superseded version simply never matches a
current proposal's `target.id` and the gate correctly asks for a fresh one.

### 3.6 Endpoints

- `POST /agent-profiles/{id}/evaluate` — path param is the draft's `AgentProfile.id`
  (same convention as `/publish`, `/unpublish`, `/propose`, all already keyed by this
  id). No request body. Loads the target row (`_load_or_404`, existing helper),
  enforces the R3 self-evaluation block (§3.4), calls `run_evaluation`, returns the
  evaluation row.
- `POST /agent-skills/{skill_key}/evaluate` — mirrors `propose_skill`'s own
  target-resolution shape: body is `{agent_id, scope, scope_id}` (same 3 fields as
  `ProposeSkillIn`, reused type), resolves the target via
  `get_latest_draft_version(..., created_by=None)` — **note**: unlike
  `propose_skill`, evaluation is NOT restricted to the caller's own draft (anyone
  eligible may evaluate ANY pending draft at that scope, since R3 specifically
  requires someone OTHER than the author) — so this needs a scope-only variant of
  the lookup. Simplest correct fix consistent with §3.1's "one version, one
  evaluation" rule: extend `get_latest_draft_version` with `created_by:
  Optional[str] = None` (default `None` = no filter, matches every inactive draft at
  this scope+key; sub-project 3's `propose_skill` call site keeps passing its own
  actor id unchanged) rather than adding a second, near-duplicate query function.

Both routes require `artifact:view` (router floor, unchanged) plus the R3 check
above — no new permission string, matching sub-project 3's own precedent of moving
authorization logic in-body rather than inventing more permission strings.

### 3.7 Frontend

- `frontend/lib/schemas/agent-studio-eval.ts` (new) — `EvaluationResult` Zod schema
  mirroring the table shape (snake_case, matching every other Agent Studio schema's
  wire convention).
- `frontend/lib/api/agent-profiles.ts` / `agent-skills.ts` — `evaluateAgentProfile`/
  `evaluateAgentSkill` client functions, same shape as `proposeAgentSkill`.
- `behavior-tab.tsx` / `skills-tab.tsx` — an "Evaluate" action next to Propose
  (visible under the same `!canManage && canPropose` — or, for an OWNER about to
  propose is moot, owners publish directly and are never gated; the Evaluate action
  is relevant to anyone about to `propose()`, so it appears wherever Propose does),
  showing PASS/FAIL + score once run; Propose stays disabled until a PASS exists for
  the CURRENT draft (mirrors `canSave`'s existing disabled-until-valid pattern in
  both tabs). For org scope specifically, if the signed-in viewer authored the
  visible draft, the Evaluate button is disabled with a tooltip explaining the R3
  self-evaluation rule, rather than letting them discover the 403 by clicking.

## 4. Explicitly out of scope (considered and rejected)

- **Live LLM-invocation "golden tasks"** (actually running the candidate draft
  against real inputs) — no preview-mode agent execution capability exists yet
  (§2); building one is a materially larger, separate prerequisite. Deferred.
- **Gating the owner's direct publish/activate path** — a real, larger product
  decision (§3.3), not what this sub-project's stated scope asks for.
- **R4 / regulated-data tiering** — no data-classification concept exists on Agent
  Studio config to hang it from (§3.4).
- **A configurable/admin-editable rubric** (`AGENT_REQUIRED_TOPICS` as DB rows
  instead of a code constant) — YAGNI for a first cut; `FORBIDDEN_PATTERNS` and
  `DESIGN_REQUIRED_SECTIONS` are both code constants today too, so this matches
  existing precedent rather than diverging from it.
- **Reusing `eval_records`/`EvalRecordService`** — considered and rejected in §2;
  different concern (fire-and-forget run telemetry vs. a durable promotion gate).

## 5. Security note (self-review finding, fixed before planning)

First draft of §3.6 had `POST /agent-skills/{skill_key}/evaluate` resolve its target
via the EXISTING `get_latest_draft_version(..., created_by=actor)` unchanged — which
would silently find NOTHING when the evaluator is (correctly, per R3) a different
person than the draft's author, since that function filters to the CALLER's own
drafts (sub-project 3's Critical #4 fix, deliberately). That would make the R3 path
permanently 404 for the one case it exists to serve. Caught during this spec's
self-review; fixed by making the `created_by` filter optional (default `None`) on
`get_latest_draft_version` rather than bypassing it or adding a parallel query
function — `propose_skill`'s existing call (which DOES want the caller's-own-draft
filter, unchanged since sub-project 3) keeps passing its own actor id explicitly, so
its Critical #4 fix is untouched.

## 6. Follow-up work this sub-project surfaces but does not do

- Gating the owner's direct-publish path (§3.3).
- A live-execution "golden task" runner, once preview-mode agent invocation exists.
- R4 / regulated-data tiering, once a data-classification concept exists.
