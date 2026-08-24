# Documentation Agent — Completion Design

Date: 2026-08-20
Scope: bring the Documentation agent to genuinely verified, enterprise-grade status per
the PRD (§21.8, unchanged in §22.8/23.10/24.8/25.6), after live hands-on verification
this session (not read-only code review) proved its core tools and graph already work —
and found one real, structural design gap (RTM traceability) that needs a decision, not
just a test.

## Context

The user explicitly rejected pattern-matching this agent against Code Review/Security's
already-verified state ("do not just fall back on things done in previous agents...
create a proper, correct, enterprise grade industry level agent"). Everything below was
therefore verified by actually running it, not by reading code and inferring it probably
works the same way its siblings did:

- **`generate_changelog`** — ran against a real git repo (created for this verification,
  4 real commits with conventional prefixes: `feat`, `fix`, `security`, `chore`).
  Correctly parsed `git log` and grouped subjects into Added/Changed/Fixed/Security.
- **`inspect_repo`** — correctly detected Python, the README, and the entrypoint file in
  the same real repo.
- **`read_repo_file`** — read real file contents from the real repo.
- **`save_document`** — wrote a real file to disk and correctly updated session state
  (`generated_docs`).
- **`read_upstream_artifacts`** — ran against a real seeded Postgres `Run` row
  (`requirements_payload`, `design_artifacts`, `security_artifacts` populated;
  `development_artifacts`, `testing_artifacts`, `code_review_artifacts` left null).
  Correctly returned the three populated artifacts verbatim and correctly returned
  `null` for the three absent ones — exactly the dual-mode contract the tool's
  docstring promises.
- **The full compiled graph** (`compiler.app.ainvoke`) — driven with only the model's
  response scripted (the same technique used for Code Review's and Security's
  `live_e2e` tests): a 3-turn script (`generate_changelog` → `save_document` → done)
  executed correctly end to end through the real graph, the real dynamic tool node, and
  the real tools, with real git-log data flowing all the way through to the saved
  document.
- **The WS message handler** (`documentation_standalone_api.py::_process_ws_message`) —
  read in full, not just the access-control layer the earlier hardening pass touched.
  Correctly binds the prepared workspace (`get_prepared` → session state), gates every
  message via `assert_agent_access_for_chat`, threads `model_id` through to graph state,
  wires real audit/langfuse instrumentation and MCP tool loading. Matches Security's and
  Code Review's proven-working pattern exactly — no rebuild needed here.
- **The frontend page** (250 lines) — a real workspace-picker dialog
  (`DocTargetDialog`, confirmed present), 6 quick-action buttons mapped 1:1 to the
  PRD's deliverables (doc_set/changelog/release_notes/rtm/run_summary/compliance),
  each sending a specific, complete prompt; a document list sidebar with type badges; a
  Markdown viewer; a download button; a gated "Open docs PR" button; a chat drawer.
  Confirmed all supporting files exist (`doc-target-dialog.tsx`, `lib/api/documentation.ts`,
  `lib/schemas/documentation.ts`) — not a stub, not broken imports.
- **`_resolve_model`** (`agents/compiler.py`) — already correctly threads `model_id`
  into *both* the BYOK-try branch and the `ChatAnthropic` fallback branch. This is
  *better* than Code Review's original implementation and pre-fix Security's (both of
  which dropped `model_id` in the fallback, fixed earlier this session) — Documentation
  never had this bug.

**What is NOT yet proven, and stays out of scope for the reason given:**
- `open_docs_pr` and the 3 SharePoint tools (`publish_to_sharepoint`,
  `list_sharepoint_documents`, `ingest_sharepoint_document`) need real external
  credentials (an ADO PAT, a configured SharePoint connector) this environment doesn't
  have — same accepted limitation as Code Review's and Security's equivalent gated
  actions. Covered by mocked unit tests instead (this plan), proving their *logic*
  (precondition checks, error paths), not a live external call.
- BYOK functionally working end-to-end — `resolve_chat_model` still doesn't exist
  anywhere in the backend (already documented in `help/portfolio-1-agent-status.md`).
  This plan adds regression tests locking in Documentation's already-correct
  `_resolve_model` *structure*; it does not implement the missing resolver.

## What this design covers

### 1. Test coverage — the primary gap

Nothing above was previously captured as a repeatable test; every verification this
session was done by hand. Two new files:

**`backend/tests/test_documentation_agent_live_e2e.py`** — mirrors
`test_code_review_agent_live_e2e.py`/`test_security_agent_live_e2e.py` exactly:
- A `_ScriptedModel` (or reused pattern) patched over `compiler._resolve_model`.
- A fixture generating a real git repo into a fresh OS temp dir at test time (not
  committed — matches the Semgrep-ignore lesson from Code Review's verification, and is
  good practice regardless of whether `git log` has the same gotcha).
- A fixture seeding a real Postgres `Run` row (org/workspace/project created with random
  UUIDs, matching this session's established test-seeding pattern) with at least
  `requirements_payload` and `security_artifacts` populated, one column left null, so the
  test proves both branches of `read_upstream_artifacts`'s dual behavior.
- A scripted turn sequence exercising `inspect_repo` → `generate_changelog` →
  `read_upstream_artifacts` → `save_document`, asserting each `ToolMessage`'s content is
  the *real* tool output (not the test's own fabricated data) — same evidentiary
  standard as the two prior live_e2e tests.

**`backend/tests/test_documentation_agent_tools.py`** — isolated, mocked unit tests:
- `generate_changelog`: unconventional commit prefixes default to "Changed"; merge
  commits excluded (`--no-merges` already in the tool); `since_ref` narrows the range;
  no-commits-yet returns an empty changelog cleanly, not an error.
- `read_upstream_artifacts`: all-null case (no tenant/project bound yet); partial case
  (already covered by the live_e2e test, referenced not duplicated here).
- `save_document`: filename sanitization (non-kebab input coerced), re-saving the same
  filename replaces rather than duplicates the `generated_docs` entry, empty content is
  refused.
- `open_docs_pr`: no generated docs yet → error; no prepared target → error (mocked, no
  real ADO call attempted).
- SharePoint tools: no connector configured → the documented "not connected" error
  message, not an exception (mocked `_sharepoint_session`).
- Two `_resolve_model` tests mirroring Security's exact pair (BYOK-first success returns
  the BYOK model; BYOK failure falls back to `ChatAnthropic` *with the caller's
  `model_id` preserved*) — regression protection for the one thing this agent already
  gets right that its siblings didn't.

### 2. RTM honesty fix

**Problem, confirmed by reading the actual Pydantic models** (not assumed): a
Requirements Traceability Matrix (requirement → design → code → test → finding) is only
*structurally* traceable on two of its five columns today.
`shared/models/requirements.py`'s `UserStory.id`/`AcceptanceCriteria.id` are real IDs, and
`shared/models/code_review.py`'s `CoverageEntry.ac_id` (inside
`CodeReviewArtifact.requirements_coverage`) is the *only* other structured link back to
them. `shared/models/design.py` is almost entirely free-text prose (`hld`, `lld`, `adrs`
as markdown strings, no requirement-ID anchor). `shared/models/testing.py` and
`shared/models/security.py` have their own self-contained IDs (`test_case_id`,
`defect_id`, `F-001`-style finding IDs) with no field referencing an AC ID at all. The
current prompt's fallback ("N/A when not knowable") doesn't distinguish a column built
from real evidence from one built by the model's own inference over prose — a real risk
for a document that's frequently used as compliance evidence.

**Fix, per the confirmed decision:** update `prompts/doc_prompt.py`'s `rtm` deliverable
description to require:
- The Requirement and Code Review columns are built from the real structured fields
  above and presented as verified.
- The Design, Development, Testing, and Security columns are either `N/A` (nothing
  knowable) or, when the model finds a plausible textual correlation in prose/context,
  explicitly labeled `"Inferred — not structurally traceable, verify manually"` rather
  than presented with the same confidence as the two real columns.

This is a prompt-only change — no other agent's output schema changes, and no new tool
is needed. Broader structural traceability (adding `ac_id`-equivalent fields to Design/
Testing/Security's own models) is a much larger, cross-agent undertaking explicitly out
of scope here, the same way fixing `resolve_chat_model` was ruled out of scope for
Security's completion pass.

### 3. `builtAgents` flip

Once the tests above pass, add `"documentation"` to the frontend's `builtAgents` array
(`frontend/app/(app)/projects/[id]/page.tsx`) — the single flag, per this session's
established precedent with Security, that turns the tile from "Coming soon" to a real,
clickable agent. No other frontend change needed — the page, dialog, and API layer are
already confirmed real and correctly wired.

## Out of scope (confirmed, not silently dropped)

- Live verification of `open_docs_pr`/SharePoint tools against real external services —
  no credentials available in this environment; covered by mocked unit tests instead.
- Making BYOK functionally work — the missing `resolve_chat_model` resolver is a
  separate, cross-agent piece of work already tracked elsewhere.
- Adding structural requirement-ID traceability to Design/Testing/Security's own output
  schemas — a much larger, multi-agent change; the RTM fix here is prompt-level honesty
  about the gap, not closing it.
- Rebuilding the WS/REST handler logic — read in full and confirmed correct against the
  proven Security/Code Review pattern; only test coverage is added.

## Files touched

- New: `backend/tests/test_documentation_agent_live_e2e.py`
- New: `backend/tests/test_documentation_agent_tools.py`
- `backend/agents_orchestrator/documentation_agent/prompts/doc_prompt.py` (§2)
- `frontend/app/(app)/projects/[id]/page.tsx` (§3, `builtAgents` array)
- `help/portfolio-1-agent-status.md` — updated once done
