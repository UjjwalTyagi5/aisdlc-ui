# Approval System — Full Audit Findings (2026-08-30)

Requested by the user after declining sub-project D (break-glass, judged
unnecessary speculative scope): "run in loops and fully check and build all
the approval system that are there in the frontend... logging in all tiers
of persona: org admin, BU admin, Contributor... build anything that has
been left (for eg integrating the approval workflow with the
notification/new for you parts on dashboards) and fix any issues."

This document is the audit phase only — no fixes applied except where noted
as a deliberate, safe, reversible exception (backend restart; see Finding 1).

Master ledger for everything already closed by sub-projects A/B/C:
`.superpowers/sdd/2026-08-29-approval-workflow/progress.md`. This audit does
NOT re-litigate anything that ledger already covers and closed — it is new
ground: real per-persona live behavior, and the notifications/dashboard
integration the user named explicitly.

---

## Status update (controller, same day)

- **Finding 1: addressed** (docs, not code) — added an explicit troubleshooting
  entry to `docs/local-setup.md` covering exactly this failure mode (commit
  `e3b20e0`).
- **Finding 3: fixed** — `create_request` now validates `workspace_id` the
  same way it validates `project_id`, refusing with `WORKSPACE_NOT_FOUND`
  (commit `bd5e7f43`). Full test suite (91 tests, `test_governance_requests.py`
  + `test_project_scoped.py`) confirmed clean.
- **Finding 4: fixed** — `DEV_LOGINS.txt`'s stale walkthrough text corrected
  locally (the file is gitignored — contains dev credentials — so this fix
  doesn't produce a commit, just a corrected local copy).
- **Finding 2 (persona decay): blocked, pending user decision.** Diagnosed
  the exact conflict (two undocumented synthetic accounts —
  `bu_admin_payments@abcbank.com` and a UUID-suffixed one — both hold
  `bu_admin` on Payments simultaneously, blocking `farah@abcbank.com` from
  being granted that seat and blocking `seed_dev_personas` from restoring
  the other 9 missing personas). The cleanup action itself (revoking both
  via the real `revoke_role()` function) was blocked by the permission
  classifier; asked the user directly whether to proceed. Not yet actioned.
- **A separate, more serious-LOOKING issue surfaced during this work, NOT in
  the original audit — investigated and resolved. Root cause: a LOCAL
  ENVIRONMENT misconfiguration, not a codebase security bug.** While
  investigating what first looked like ordinary test flakiness in
  `test_notifications.py`/`test_approval_requests.py`, a notification row
  belonging to the real dev tenant (`8d5bd6a3-7e07-46ce-8416-cada90dead79`)
  appeared in a query scoped to a completely different, freshly-generated
  random test tenant, despite `notifications` having correctly-configured
  `FORCE ROW LEVEL SECURITY`. The original pooled-connection-bleed
  hypothesis was DISPROVEN by direct repro (two sequential sessions against
  fresh random tenants showed correct, GUC-scoped isolation — and the
  engine uses `NullPool`, so there is no connection reuse across sessions
  to bleed through in the first place).

  **Actual root cause, confirmed:** this worktree's `backend/.env` had
  `POSTGRES_CONN_STRING` pointed at the Postgres **superuser** role
  (`postgres`) instead of the restricted `sdlc_app` role the codebase's own
  migrations grant permissions to and `docs/local-setup.md` documents as
  the intended app role (`sdlc_app` is deliberately `NOBYPASSRLS`,
  specifically so `FORCE ROW LEVEL SECURITY` is a real boundary). A Postgres
  superuser always bypasses RLS entirely, regardless of `FORCE ROW LEVEL
  SECURITY` — confirmed directly (`rolbypassrls: true` for `postgres`,
  `false` for `sdlc_app`). This meant RLS was **never actually being
  enforced** at the database level for ANY query against this local dev
  database — a constant bypass, not an intermittent leak — for however
  long `.env` had been misconfigured this way. `sdlc_app` itself existed
  correctly (right RLS attributes) but had no password/LOGIN set, so it was
  unusable as-is.

  **Important: this was never a live production risk.** `process_api.py`
  already has a dedicated boot-time guard (`_check_...` near
  `BYPASSRLS startup guard`) that refuses to start in enterprise mode if
  the app's DB role has SUPERUSER or BYPASSRLS — deliberately skipped only
  in local/non-enterprise dev mode, exactly the mode this was. The
  codebase's own authors already identified and named this exact risk
  class (referenced as "Pitfall 5" / T-M7.1-16 in code comments) and
  explicitly chose not to force it in local dev, so developers without a
  properly-configured restricted role locally aren't blocked from working.
  `tests/test_rls_isolation.py`'s own docstring states this outright:
  *"get_db_session_superuser() does NOT bypass RLS unless the connecting DB
  role is BYPASSRLS/superuser — the production app role is restricted, so
  seeding must go through the tenant GUC."*

  **What this DOES mean:** every "live-verified against the real dev
  backend" claim made anywhere in this session that specifically depended
  on RLS as the enforcement mechanism (rather than application-level
  scoping, which was mostly still correct and independently tested) was
  running with that database-level backstop silently absent. No evidence
  found that this produced any FALSE positive in this session's actual
  findings — application-level scoping bugs were still caught correctly by
  every fix made — but it does mean the database-level defense-in-depth
  layer specifically wasn't actually being exercised.

  **Fixed:** `sdlc_app` given a password and LOGIN capability locally
  (`ALTER ROLE ... WITH LOGIN PASSWORD ... NOSUPERUSER NOBYPASSRLS`);
  `scripts.grant_app_role` re-run to confirm all grants current; `.env`'s
  `POSTGRES_CONN_STRING`/`POSTGRES_SYNC_CONN_STRING` repointed at
  `sdlc_app` (`POSTGRES_MIGRATIONS_CONN_STRING` correctly left as
  `postgres`, per the documented design — migrations legitimately need
  superuser DDL rights). `.env` is gitignored, so this fix is local-only,
  not a commit. The running dev backend was restarted to pick up the
  corrected connection; `/health` confirms `"postgres":"ok"` under the new
  role. One genuine casualty found and fixed: a test seed helper
  (`_seed_inactive_anthropic_provider`'s successor in
  `test_governance_requests.py`) was inserting into the FORCE-RLS
  `model_providers` table via `get_db_session_superuser()` instead of
  `get_db_session_for_tenant()` — this only ever "worked" because RLS was
  bypassed; fixed to match the pattern every other FORCE-RLS-table-seeding
  helper already correctly uses. Full targeted suite (`test_rls_isolation.py`,
  `test_rls_coverage.py`, `test_governance_requests.py`, `test_notifications.py`,
  `test_approval_requests.py`, `test_project_scoped.py` — 118 tests) passes
  clean under the corrected, RLS-enforcing connection. Full repo-wide
  `pytest tests/` run (1795 passed, 9 skipped, 7 xfailed, 17 xpassed, 18
  failed on the first pass) surfaced 15 more real instances of the same
  pattern across `agent_profiles`/`agent_skills` evaluation-gate tests and
  the `org_model_grants`→`integration_grants` migration test's
  verification reads, plus one test
  (`test_decide_belt_and_suspenders_blocks_an_unevaluated_target`) that
  needed a real seeded project to satisfy `create_request`'s now-stricter
  `PROJECT_NOT_FOUND` check (see Finding 3) — all fixed, commit
  `8be7e5d6`. Two more failures (`test_project_business_unit.py`,
  `test_project_track.py`) turned out to be a genuinely pre-existing,
  unrelated test-drift bug dating to 2026-08-25 (`monthlyBudgetUsd` became
  a required field on project creation; two test files were never
  updated) — fixed anyway since it was cheap and clear-cut, commit
  `b7182d10`. Three failures (`test_pipeline_session.py`) were confirmed
  flaky — passed cleanly on isolated re-run, no fix needed. Three
  remaining failures (`tests/copilot/*`) are a genuinely pre-existing,
  unrelated environment gap — this local dev environment's Anthropic API
  key is invalid/missing, causing real LLM calls in those specific
  integration tests to fail; out of scope for this session's work, not
  fixed. **Second full sweep confirmed clean**: 1807 passed (up from 1795),
  6 failed — exactly the 3 known out-of-scope API-key failures plus the 3
  `test_pipeline_session.py` tests, which failed again under full-suite
  load but passed cleanly in an isolated re-run both times, confirming
  order/resource-contention flakiness rather than a real regression (the
  same flakiness class already documented earlier this session by the
  model_provider_access fix's own implementer). Nothing further to fix.

---

## Finding 1 — CRITICAL (process, not code): the dev backend can silently
## run stale code for extended periods

**Confirmed live, reproduced directly.** The backend process running at
session start (PID 23848, started 2026-08-30 15:51:17) was still running
16+ minutes after commit `6c02a513` landed (16:07:10) — a commit that fixes
exactly the workspace-scoping bug this audit was testing. A live raise
against that stale process returned the OLD, buggy behavior (client-sent
`workspaceId` stored verbatim, unresolved against the project) even though
the fix's own automated tests pass and the source file on disk was correct.
Restarting the process (`uv run uvicorn process_api:app --port 8001`, no
`--reload`, to pin it to this exact commit for the remainder of this audit)
immediately produced the correct, fixed behavior on the identical request.

**Why this matters beyond this one test:** every "live-verified against the
real dev server" claim made anywhere in this session — by the controller or
by any dispatched implementer — is only as trustworthy as the server's
actual freshness at the moment of that specific test. `docs/local-setup.md`
documents `--reload` + `watchfiles` as the standard way to run it, which
should make this a non-issue in principle, but this session has now hit the
concrete failure TWICE independently: once here, and once earlier
(documented in the model_provider_access fix's own report — "the first
attempt hit a stale `--reload` dev server still running old buggy code").
Two independent hits of the same failure mode is a pattern, not a fluke.

**Suggested fix (process, not code):** before any live-verification step in
this codebase's workflow, confirm the running backend's start time postdates
the commit under test (`Get-Process -Id <pid> | Select StartTime` vs.
`git log -1 --format=%ci <commit>`), or simply restart it immediately
before testing. Worth adding as an explicit line item in
`docs/local-setup.md`'s troubleshooting section, since the existing
`watchfiles` warning covers a DIFFERENT reload failure mode (slow CPU-bound
polling) and does not warn that reload can also just not have caught up yet.

---

## Finding 2 — IMPORTANT: seeded dev personas have decayed; most delivery
## tiers cannot currently be live-tested at all

`DEV_LOGINS.txt` documents 4 accounts. `backend/scripts/seed_dev_personas.py`
actually defines 14. Querying the live dev DB directly:

| Email | Documented role | Exists? | Actual role binding |
|---|---|---|---|
| orgadmin@abcbank.com | org_admin | yes | org_admin ✓ |
| farah@abcbank.com | bu_admin, Payments | yes (can log in) | **none — zero role bindings** |
| marcus@abcbank.com | bu_admin, Lending | yes | bu_admin/Lending ✓ |
| ana@abcbank.com | project_admin, Core ledger | yes | project_admin/Core ledger ✓ |
| diego@abcbank.com | developer, Core ledger | yes | developer/Core ledger ✓ |
| priya, iris, ingrid, hana, lena, bruno, luca, sofia, amara (9 accounts) | ba/architect/qa/security_engineer/devops_engineer/data_engineer/scrum_master/project_admin(2nd project)/contributor | **none — no user row at all** | — |

**Two compounding problems found, live:**

1. **Farah, the documented Payments BU Admin, holds no role at all.**
   Meanwhile TWO other, undocumented accounts —
   `bu_admin_payments@abcbank.com` and a UUID-suffixed synthetic account
   (`bua-9ca53c05-...@abcbank.com`) — currently BOTH hold `bu_admin` on the
   Payments business unit simultaneously. `shared/authz/grant.py`'s
   `_assert_single_bu_admin` exists specifically to prevent a unit having
   more than one BU Admin — this live state is a confirmed, present
   violation of that exact invariant, evidently reached by some path other
   than `grant_role` (a raw-SQL seed or test-data insert, most likely),
   since `grant_role` itself refuses the second grant when called (this is
   exactly what blocked the seed script re-run below).
2. **Re-running the seed script to restore the missing 9 personas fails
   outright**: `python -m scripts.seed_dev_personas` crashes with
   `UnitAlreadyAdministeredError` the moment it tries to (re-)grant Farah
   the Payments seat, because of finding (1) above. The script has no
   partial-progress recovery, so it cannot currently be used to repair
   itself.

**Practical consequence for this audit:** Security Engineer, BA, Architect,
QA, DevOps Engineer, and Data Engineer tiers — i.e. every role this session's
`agent_access` stage-two effect (sub-project A, Task 8) exists to serve —
could NOT be live-tested in this pass. Neither could the "Amara has no role,
Farah grants her one" onboarding walkthrough DEV_LOGINS.txt itself describes
as the canonical first thing to try. This is a real coverage gap in the
audit the user asked for, not a shortcut taken.

**Suggested fix:** manually resolve the Payments BU-Admin conflict (decide
which of the two synthetic accounts is safe to deactivate/remove, or
deactivate both and let the seed script grant Farah cleanly), then re-run
`python -m scripts.seed_dev_personas` to restore the other 9 documented
personas. Until that happens, re-run this audit's persona sweep for the six
missing delivery roles specifically.

---

## Finding 3 — IMPORTANT: `create_request` validates `project_id` but never
## `workspace_id` — a bogus workspace produces a permanently undecidable request

**Confirmed live, reproduced directly.** Raised `access_request` as `diego`
with `workspaceId` set to the tenant's own id (not a real row in
`workspaces` at all — a plausible real mistake for a frontend to make, e.g.
a stale/undefined variable) and no `projectId`. The request was accepted
(201), stored with `current_approver_role = 'project_admin'`, and is now
permanently stuck: no real `role_bindings` row has that bogus `scope_id`, so
`decider_covers_scope`'s project-less fallback (any live `project_admin`
bound within the request's own business unit) can never match anyone — the
request is undecidable by ANY project_admin in the tenant, forever, with no
error at raise time to warn the caller.

This is the mirror-image of the bug commit `6c02a513` just closed for
`project_id` (a bogus `projectId` now correctly refuses with
`PROJECT_NOT_FOUND`) — the same validation was never extended to
`workspace_id`, which is arguably the MORE exposed case, since every request
type carries a `workspace_id` while only some carry a `project_id`.

**Suggested fix:** in `create_request`, validate `workspace_id` against a
real row in `workspaces` (scoped to the tenant) the same way the recent fix
now validates `project_id`, refusing with a clear `GovernanceError` (e.g.
`WORKSPACE_NOT_FOUND`) rather than silently accepting an unroutable request.
Low-risk, small, direct sibling of an already-shipped, already-tested fix.

---

## Finding 4 — MINOR: `DEV_LOGINS.txt`'s walkthrough text no longer matches
## actual routing behavior

Step 3 of its own "WHAT TO TRY" section: *"As ana@, raise a budget increase
from Cost & Budget. It skips the BU Admin and goes to the Org Admin."* Live
test: `ana` (project_admin) raising `budget_increase` for her own project
correctly routes to `bu_admin` FIRST (confirmed: `currentApproverRole:
"bu_admin"`), matching `routing.py`'s own documented design ("a Project
Admin's [ask] climbs to their BU Admin, a BU Admin's to the Org Admin") and
exactly the behavior sub-project C's Task 1 (the new project-tier budget
endpoint) was built to produce. The doc text likely predates that work and
was never updated — a documentation fix, not a code fix.

---

## Confirmed working — no findings (live-verified, not just read from code)

- **Raise → visible in the correct decider's queue → decide → effect
  fires**, tested end-to-end for: `access_request` (developer → project
  admin, project-scoped correctly per the just-shipped fix), `budget_increase`
  and `access_request` escalation (project_admin → bu_admin → org_admin, via
  a real `/escalate` call), `model_provider_access` (bu_admin → org_admin).
- **Cross-scope isolation holds live**: `marcus` (Lending bu_admin) does NOT
  see Payments-project requests in his queue; queue sizes differ correctly
  per viewer.
- **Notifications are real and correctly delivered**, not a stub: `ana`
  received a `request_approval_required` notification with the correct
  title and `href: "/approvals"` within the same request/response cycle as
  diego's raise landing in her queue.
- **The notification bell** (`frontend/components/app/notifications-bell.tsx`)
  is real, mounted in `top-bar.tsx` (present on every authenticated page),
  polls every 30s, sums a live SSE-driven count with the durable
  server-stored count, deep-links via `n.href`, and distinguishes a failed
  fetch from a genuinely empty list — not the two hard-coded rows its own
  comment says it used to be.
- **The dashboard has a real "Attention" list**
  (`frontend/app/(app)/dashboard/page.tsx`), pulling from both
  `listApprovals` (agent gates) and `listGovernanceApprovals` (governance
  requests) and surfacing overdue items with real hrefs — not a stub, and
  not something that needed building from scratch as the user suspected.
- **General "Request X" button sweep**: spot-checked `projects/[id]/page.tsx`'s
  `agent_access` request button (the phase-pipeline's locked-tile action,
  not touched by sub-project C) — correctly scoped with both `projectId`
  and `workspaceId` set from the real project object, matching the
  already-correct pattern cited elsewhere in this session's ledger. No new
  dead button found here.

---

## Follow-up: the three previously-blocked items, now live-tested (2026-08-30)

All three closed after Finding 2's persona restore:

- **`agent_access` stage-two decide path**: live-verified end-to-end for
  `hana@abcbank.com` (security_engineer) — diego raised
  `agent_access`/phase=`security` on Core ledger, ana@ (project_admin)
  decided stage one, hana@ decided stage two, both 200s, final status
  `approved`. Confirmed the real effect landed:
  `role_bindings.extra_agents` for diego now includes `"security"`. The
  mechanism is identical in shape for the other five delivery roles
  (`routing.AGENT_OWNER_ROLE` maps each phase uniformly) and is already
  exhaustively covered by the automated suite
  (`test_agent_access_stage_two_refuses_the_wrong_projects_owner` etc.) —
  one live spot-check plus the passing automated suite is treated as
  sufficient; not repeated for all six roles.
- **Onboarding walkthrough**: live-verified — `farah@abcbank.com`
  (bu_admin, Payments) granted `amara@abcbank.com` (previously
  role-less) the `ba` role via `POST /workspaces/{id}/members`, 201.
  Reverted afterward via the real `DELETE /workspaces/{id}/members/{id}`
  endpoint (204) to restore amara's documented "no role yet" seed state,
  so the walkthrough DEV_LOGINS.txt describes stays reproducible for
  whoever tries it next.
- **Cross-project isolation (`sofia@abcbank.com`)**: live-verified —
  sofia sees exactly one project (Mobile onboarding journey) and her
  governance queue only ever shows that project's own business unit
  (Lending). Initially looked like a leak (queue entries showed
  `workspaceName: "Lending"`, not "Payments"), but direct DB check
  confirmed this is correct: Mobile onboarding journey's real
  `workspace_id` genuinely IS Lending — sofia holds exactly one role
  binding (`project_admin` on her own project) and nothing else. No bug;
  the scoping worked exactly as designed.

---

## Priority summary

1. **Finding 2** (seed/persona decay) is the actual blocker for finishing
   this audit's own stated scope — "logging in all tiers of persona" is
   currently impossible for 6 of 10 roles. Fix this first if the goal is a
   genuinely complete sweep, not a partial one.
2. **Finding 3** (missing `workspace_id` validation) is a small, direct,
   low-risk fix — the natural next line after commit `6c02a513`, same shape,
   same file, same tests to write.
3. **Finding 1** (stale server) is a process discipline fix, not code — but
   worth documenting explicitly so it doesn't silently invalidate the NEXT
   round of live verification too.
4. **Finding 4** is a two-line doc fix, lowest priority.
5. Everything under "Confirmed working" needed no further hardening in this
   pass — the notification/dashboard integration the user was specifically
   worried about is real and functioning, not the gap they suspected.
