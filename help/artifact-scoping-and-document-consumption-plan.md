# Project- and agent-scoped documents, and letting one agent read another's

## Context

Two things are missing, and the second is the one that makes the feature worth building.

**A document has nowhere to belong.** `artifacts.run_id` is NOT NULL and there is no
`project_id` or agent column, so a document is only "the Design agent's" by accident of
which run produced it, and a project-wide document — a policy, a compliance standard —
cannot exist at all. Nor can a person add one: there is no upload endpoint.

**No agent can read a document.** Every "artifact" an agent reads is a JSONB stage
payload. `artifacts` is an entirely UI-facing store; the PM agent cannot see a
Requirements PDF whether or not anybody approved it.

What you asked for: documents scoped to a **project** or to a specific **agent** with
the producing agent visible; **manual upload**; a view of what is approved, **by whom
and when**; and **one agent consuming another's documents** under a proper approval
mechanism.

Verified by reading the code on 2026-09-07.

### What already exists, and is the reason this is small

- `artifact_store.blob_path_for` already emits
  `{tenant}/{bu}/{project}/{agent}/{run}/{type}/{filename}` and already takes
  `project_id` and `agent`. The storage layout anticipated this; the row never caught up.
- The `_pending` → approve → promote flow works: bytes wait under a pending prefix with
  `blob_url` NULL, and approval moves them.
- `artifacts.approved_by` / `approved_at` are written on every approval and **never
  surfaced** — `ArtifactOut` has no such field, and neither does the frontend schema.
- **`artifact_versions.covers` is a JSONB list of `artifacts.id`, added for exactly this
  and never populated.** The approval mechanism for cross-agent consumption already
  exists; documents need to join it rather than get a second one.
- `shared/tools/document_tools.py::extract_file_text` handles pdf/docx/txt/md/csv/xlsx
  and is already used by several agents for chat attachments.

### What is wrong today

- **The agent's owner cannot approve.** `_artifact_for_decision` demands
  `require_permission("approve")` AND `assert_can_administer_project`, so an Architect or
  QA who is not a project admin is refused their own stage's documents.
- **Half the Requirements list is not artifacts.** The `story · v1` rows are
  `story_artifacts_from_run` — projections of `Run.requirements_payload` with synthesised
  ids, no blob, no row. They can never be approved or carry an approver.

---

## The rule, in one table

| Document | Who may read it |
|---|---|
| project-level (`stage IS NULL`), approved | every agent |
| agent-level, **covered by a published version** | every agent |
| agent-level, approved but not covered | its own agent only |
| pending or rejected | nobody |

A document being *approved* means "fit to exist in the project's record". A document
being *covered by a published version* means "part of the signed-off unit this stage
handed downstream". They are different questions and the gate exists for the second.

## Decisions taken

- `project_id` NOT NULL, `stage` nullable (NULL = project-level), `run_id` nullable.
- Manual uploads are **pending until approved** — same `_pending` path as generated ones.
- Approver is the **agent's owner OR a project admin**; project-level needs project
  administration.
- Documents are consumed by **riding the published version** (`covers`), not by a second
  approval system.
- Project-level approved documents are readable by every agent, no request.
- An agent receives **metadata, then fetches text on demand** — inlining a 200-page PDF
  into every upstream read would cost context on every turn.
- The screen **splits Documents from Stories**.

---

## Phases

### Phase 1 — let a row say which project and which agent

Migration `0052_artifact_project_scope`:

- `project_id uuid NOT NULL`, FK ON DELETE CASCADE
- `stage varchar(32) NULL`
- `run_id` becomes nullable, FK ON DELETE SET NULL — a document outlives the run that
  made it, and an uploaded one never had one
- `uploaded_by varchar(255) NULL` — who put it there, distinct from who approved it
- index `(project_id, stage)`

**BACKFILL BEFORE THE NOT NULL.** Adding a NOT NULL column to a populated table fails
outright: the column lands nullable, fills from `runs.project_id` / `runs.stage`, then
tightens — in one migration, so a half-migrated database cannot exist.

`list_artifacts_for_project` then filters `Artifact.project_id` directly instead of
through the `Run` join, and `store_artifact` records what it is already handed.

Files: `backend/migrations/versions/0052_artifact_project_scope.py`,
`backend/shared/models/orm.py`, `backend/shared/services/artifact_store.py`,
`backend/shared/routers/artifacts.py`.

### Phase 2 — let the agent's owner approve

    agent-level  (stage set)    artifact:approve_<stage>  OR project administration
    project-level (stage NULL)                               project administration

The stage comes from the ROW, not the path, so `require_stage_approval()` cannot be a
route dependency here — the check moves into `_artifact_for_decision`. The route keeps
`require_permission("approve")` as its floor so the boot scan still sees it protected.

Files: `backend/shared/routers/artifacts.py`.

### Phase 3 — say who approved it, and when

`ArtifactOut` gains `approvedBy`, `approvedAt`, `uploadedBy`, `stage`; the frontend
`Artifact` schema gains the same. (The `approvedBy`/`approvedAt` already in
`lib/schemas/artifact.ts` belong to `DeployEnv` — unrelated.)

Files: `backend/shared/routers/_schemas.py`, `frontend/lib/schemas/artifact.ts`.

### Phase 4 — upload a document by hand

`POST /projects/{project_id}/artifacts/upload` (multipart), gated on `run:create` —
adding a document is producing work, not accepting it.

- `stage` optional: absent means project-level
- validate with `attachment_store.ALLOWED_ATTACHMENT_EXTS` / `MAX_ATTACHMENT_BYTES`
- bytes to the `_pending` path, `approval_status='pending'`, `blob_url` NULL,
  `uploaded_by` recorded, `run_id` NULL

**AND THE BFF ROUTE, IN THIS PHASE.** The browser never reaches FastAPI; it calls
`/api/...`. A backend route with no handler under `frontend/app/api/` returns Next's own
404 and reads as a backend fault — that shipped once already this week. Multipart needs
its own handler, not `forward()`, which sends JSON.

Files: `backend/shared/routers/artifacts.py`,
`frontend/app/api/projects/[id]/artifacts/upload/route.ts`,
`frontend/lib/api/artifacts.ts`.

### Phase 5 — the screen

    Published version   v2 · signed off by bruno · 4 Sep

    Documents                                        [ Upload ]
      BRD.docx        requirements · approved · bruno · 4 Sep   [Download]
      policy.pdf      project-wide · approved · pm@x   · 2 Sep  [Download]
      scope.pdf       requirements · pending                    [Approve] [Reject]

    Stories (15)      from the last board pull
      Performance…    story · v1

Two sections because they are two different things. The scope badge
(`requirements` / `project-wide`) is what makes "which agent uploaded this" visible. A
pending document shows no download link — its bytes are in `_pending` and `stored` is
already false.

Files: `frontend/components/app/document-list.tsx` (new, beside `artifact-list.tsx`),
`frontend/app/(app)/projects/[id]/requirements/page.tsx`.

### Phase 6 — one agent reads another's documents

**Covers is chosen when the version is FROZEN, not when it is published.** The freeze
trigger makes `covers` immutable along with the payload, and that is correct: the signed
unit is "this payload plus these documents", and letting the document list change after
freezing would mean the thing approved was not the thing signed. The UI's freeze step
offers the stage's approved documents to tick.

- `read_upstream` returns a `documents: [...]` list alongside the payload: the covered
  documents of the published version, plus every approved project-level document
- a new agent tool `read_document(artifact_id)` fetches the bytes and runs
  `extract_file_text`, capped with an explicit truncation marker rather than silently
  cut. It refuses anything the table above does not permit — the same fail-closed answer
  `read_upstream` gives, with a reason
- evidence: `artifact_consumptions` gains a nullable `artifact_id`, `version_id` becomes
  nullable, and a CHECK requires one of the two. Without it a project-level document read
  has no version to point at and would go unrecorded

Files: `backend/migrations/versions/0053_document_consumption.py`,
`backend/shared/services/artifact_versions.py` (`read_upstream`, `run_evidence`),
`backend/shared/services/artifact_consumption.py`,
`backend/shared/tools/document_tools.py` (reuse), the four agent tool modules.

### Phase 7 — the same shape on every agent

The panel takes `projectId` + `phase`; the tool resolves its own stage. Nothing
per-agent, because the permission and the scope both derive from the phase.

---

## What must not be built

- **A second approval system for documents.** They ride the version. Two things called
  approval on one screen is how people stop trusting either.
- **An upload that skips `_pending`.** "Approved" would then mean two different things
  in one list.
- **Inlining document text into every upstream read.** An agent that wanted the payload
  would pay for every attached document on every turn.
- **An approver column on the story rows.** They have no approver and never will.

## Verification

**Migration, against a database built from scratch** (`sdlc_product_test`): the chain
applies, `0052` backfills a populated `artifacts` table without violating the NOT NULL,
and both migrations round-trip down and back up.

**Backend tests**, beside the existing artifact suites:
- `test_artifact_scope.py` — project-level and agent-level rows; a project-level document
  is not returned when filtering by phase; RLS isolates both
- `test_artifact_upload.py` — a bad extension and an oversized file are 400; the row is
  `pending`, `blob_url` NULL, bytes under `_pending`
- extend `test_artifact_approval.py` — an Architect approves a design document and is
  REFUSED a deployment one; a project admin approves a project-level one; a developer
  holding neither is refused
- `test_document_consumption.py` — the four rows of the rule table, each asserted
  directly; an uncovered agent-level document is invisible to another agent; a revoked
  or rejected one is invisible to everyone; the read is recorded

**Frontend:** `__tests__/bff/every-api-path-has-a-proxy.test.ts` covers the upload route
automatically — it compares every path `lib/api` calls against the handlers that exist.

**End to end in the UI**, as bruno@abcbank.com on `test-demo`: upload a PDF to
Requirements; confirm it is listed and NOT downloadable; approve it as the BA; confirm
the bytes move and the row reads "approved · bruno · <date>"; freeze a Requirements
version with that document ticked and publish it; then confirm the PM agent's
`read_upstream` lists it and `read_document` returns its text — and that a second
document left uncovered is not offered.

**Do NOT run the full backend suite against the dev database.** `pytest` now refuses
without `backend/.env.test`; keep it that way.

## Open

- Whether re-uploading a document of the same name supersedes the previous one or
  creates a second row. Recommendation: a second row for now. `artifact_versions` already
  owns versioning for payloads, and two version systems on one screen is how people stop
  reading either.
