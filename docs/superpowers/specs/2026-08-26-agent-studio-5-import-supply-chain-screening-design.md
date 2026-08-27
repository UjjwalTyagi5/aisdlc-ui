# Agent Studio Sub-Project 5 — Import + Supply-Chain Screening

**Status:** design, self-reviewed, ready for planning.

## 1. Problem statement

Every Skill and Behavior draft built so far in Agent Studio is TYPED by hand into
the editor. There is no way to bring in content that originated somewhere else —
another Business Unit's already-working skill, or a document a Business Unit Admin
received from outside the platform — without retyping it. The master ledger's
confirmed gap: "Zero import/supply-chain-screening flow (no import endpoint, no
credential scanner wired to skills, no provenance/allowlist concept)."

This sub-project gives a Business Unit Admin an **Import** action for Skills:
bring in content from another Business Unit they administer, or from an external
source, run it through three screens (prompt-injection, credential leakage,
provenance), and land it exactly where a normal create would — an active row if
they own the target tier, an inactive draft awaiting the owner's approval if they
don't (identical to every other write path this platform already has).

## 2. What already exists (survey, do not rebuild)

- **`FORBIDDEN_PATTERNS`** (`agent_profiles.py`) — the existing prompt-injection
  deny-regex list, already shared by Behavior and Skills at write time, and reused
  again by sub-project 4's evaluation scoring. A third reuse here, unchanged.
- **`_SECRET_PATTERNS`** (`agents_orchestrator/development_agent/tools/
  sandbox_policy.py`) — `list[tuple[re.Pattern, str]]`, pairs of (detector,
  redaction-replacement) for GitHub PATs, OpenAI/Anthropic-shaped keys, `Bearer`
  tokens, `password=`/`secret=` assignments, and credentials embedded in clone
  URLs. Built for redacting agent tool output, not for gating an import — this
  sub-project reuses the DETECTION half (the compiled patterns) for a pass/fail
  decision, not the redaction half.
- **`administered_workspace_ids(db, request)`** (`shared/authz/read_scope.py`) —
  the workspaces a caller genuinely administers (a live `bu_admin`/`org_admin`
  binding, per that module's own "governing a unit is what reaches across its
  projects" doctrine). This is the exact primitive "another BU they administer"
  needs — already used elsewhere for admin-write gating (`assert_can_write_
  workspace`), never yet wired into Agent Studio.
- **The inactive-draft mechanism** (sub-projects 2-3) — `create_custom_skill`/
  `update_custom_skill`'s `activate: bool` parameter, and `propose_skill()`'s
  governance-request filing. **This is the load-bearing reuse for this whole
  sub-project**: "staged until confirmed" is not a new concept to invent — it is
  exactly what an inactive draft already is. An import that lands as
  `activate=owns` and, when `owns` is false, is proposed through the exact same
  `agent_default_*` governance flow, gets confirmation/approval for free from
  infrastructure that already exists, is already reviewed, and is already tested.
- **`OrgModelGrant`** (`frontend/lib/schemas/model.ts`) — the platform's one other
  precedent for "who gets to bring a new thing into the catalogue": an Org Admin
  makes an explicit, auditable grant (`global` or scoped to named units); a BU
  Admin cannot widen it themselves. Its own docstring explicitly frames the
  alternative — a BU-Admin-edited allow-list — as the thing this design replaced,
  because letting a unit's own admin decide what enters is "the opposite of what
  'the Org Admin governs the catalogue' means." The provenance/allowlist screen in
  this sub-project follows the SAME governance shape for import sources, for the
  same reason.
- **Confirmed gap**: no code anywhere in this repo integrates with an external
  registry, marketplace, or repository host (no GitHub App, no OAuth flow, no
  webhook receiver). "Import from an external source" therefore cannot mean "the
  platform fetches content live from somewhere" — that is a materially larger,
  separate integration project. See §3.4 for the scope this sub-project actually
  builds instead.

## 3. Scope

### 3.1 What "import" means here

One new action on Skills only (Behavior is a single free-text prompt with no
natural "which skill" unit to import — Skills' SKILL.md-shaped, versioned,
per-key content is what makes import meaningful; extending to Behavior is out of
scope, see §4): a Business Unit Admin supplies a skill's content (display name,
description, when-to-use, body) plus where it came from, targeting a scope they
have SOME standing on (owns or may_propose, via the existing `resolve_actor_
tier_access` — identical authorization to every other Skills write). The import
runs through three screens (§3.2) before it is allowed to touch the database at
all. On a pass, it is written via the EXACT SAME `create_custom_skill`/
`update_custom_skill` path an ordinary "New skill"/"Edit" already uses —
`activate=owns` — so an owner's import goes live immediately and a non-owner's
lands inactive, proposable through the existing `propose_skill()` flow. Import is
a different FRONT DOOR onto the same write path, not a parallel one.

### 3.2 The three screens, in order

All three run server-side, synchronously, inside the import route — a failure at
any screen refuses the import outright (422) before anything is written; nothing
is staged in a "quarantined" state for later review; there is no partial import.
This matches sub-project 4's own precedent (evaluation is refused early, not
recorded-then-flagged) rather than inventing a new lifecycle state.

1. **Prompt-injection screen** — reuses `FORBIDDEN_PATTERNS` exactly as
   `lint_skill_fields` already does for a normal create/update. Not a new check;
   import goes through the SAME lint call every other write already does, so this
   "screen" is really just confirming import doesn't bypass it (a real risk if
   import were built as a separate code path instead of reusing `create_custom_
   skill`).
2. **Credential screen** (NEW use of existing patterns) — `shared/eval/
   import_screening.py`'s `scan_for_credentials(text) -> list[str]` runs every
   `_SECRET_PATTERNS` detector (the regex half only, no redaction) against the
   imported body/description/when_to_use fields; any match is a hard refuse
   (`422 CREDENTIAL_DETECTED`), listing which pattern categories matched (never
   the matched text itself — an error message that echoes back a leaked secret
   would defeat the point of catching it).
3. **Provenance screen** (NEW) — the declared source must be one of:
   - **`same_tenant_bu`**: another workspace the importer administers
     (`administered_workspace_ids` — `None` means org-wide, so an org_admin's
     import always passes this leg; otherwise the declared source workspace id
     must be IN that list) — this alone IS the provenance check for a cross-BU
     import; no separate allowlist needed, since "you administer both ends" is
     already the trust boundary. Not administered -> `422 SOURCE_NOT_ALLOWED`.
   - **`external`**: a source URL/identifier the importer supplies, checked
     against a new, Org-Admin-governed allowlist (§3.3) — mirroring
     `OrgModelGrant`'s governance shape. No entry matching the declared source ->
     `422 SOURCE_NOT_ALLOWED` (same code as the same_tenant_bu case — one
     provenance-failure code for the whole screen, since both mean the same
     thing to the caller: "this source is not one you may import from"). A BU
     Admin cannot self-approve their own import source, the same way they
     cannot self-grant a model.

### 3.3 New table: `import_source_allowlist`

Org-Admin-managed (mirrors `OrgModelGrant`'s "the Org Admin governs the
catalogue" doctrine — see §2). One row per approved external source pattern:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `tenant_id` | UUID | RLS key |
| `source_pattern` | String | a URL-prefix or domain string (e.g. `https://github.com/my-org/`) — a declared source matches if it STARTS WITH an allowlisted pattern (simple, auditable, no regex-injection surface from an admin-typed string) |
| `label` | String | admin-facing name for the entry ("Acme's internal skill library") |
| `created_by` | String | |
| `created_at` | DateTime(tz) | |

Write access: `admin:*` only (Org Admin) — reuses the existing wildcard check
already used elsewhere for org-wide governance actions (`is_org_wide`/`ORG_WIDE_
PERMISSIONS` in `read_scope.py`), not a new permission string. Read access: the
router floor (`artifact:view`) — a BU Admin needs to see the list to know what
they may declare as a source, the same way they can already see the model
catalogue they don't get to edit.

### 3.4 Explicitly NOT built (the "external source" boundary)

- **No live fetch from any external system.** "External source" means the
  importer PASTES content they obtained elsewhere and DECLARES where it came
  from (a URL string checked against the allowlist) — there is no crawler,
  no API client, no OAuth. Building real external-source fetching (a GitHub
  App, a marketplace API) is a separate, materially larger integration project
  this sub-project does not attempt.
- **No quarantine/staged-for-manual-review state.** A screen either passes or
  the import is refused outright — see §3.2's opening paragraph. A "flagged,
  pending security review" intermediate state is a real, larger feature
  (who reviews it? what's the SLA? does it notify someone?) this sub-project's
  three explicitly-named scanners do not need.
- **No Behavior (prompt) import** — see §3.1.
- **No new governance request type** — cross-BU/non-owner imports reuse
  `agent_default_*` exactly like every other non-owner Skills write, per §2's
  reuse principle. No `skill_import_*` type family.

## 4. Endpoint

`POST /agent-skills/import` — new route (not `{skill_key}/import`, since import
is how a NEW skill_key enters a scope, mirroring the shape of the existing
skill-key-less `POST /agent-skills` create route, not the single-key routes).

Body: `{agent_id, scope, scope_id, skill_key, display_name, description,
when_to_use, body, source: {kind: "same_tenant_bu" | "external", workspace_id?,
url?}}`. Response: identical shape to `POST /agent-skills` (a `SkillDetail`) —
importing IS creating, from the response consumer's point of view.

Sequencing inside the route: authorize (`resolve_actor_tier_access`, owns or
may_propose at the target scope — identical to `create_skill`'s existing check)
-> validate scope/agent -> run the three screens (§3.2, in the stated order,
refusing on the first failure) -> lint (`lint_skill_fields`, the same call
`create_skill` already makes) -> duplicate-key check (the same existing check) ->
`create_custom_skill(..., activate=owns)`. This is deliberately the SAME sequence
`create_skill` already runs, with the three screens inserted before the existing
lint call — import is `create_skill` plus provenance, not a rewrite of it.

## 5. Frontend

- A "Import" button next to "New skill" in `skills-tab.tsx`, visible under the
  same `canManage || canPropose` gate the existing "New skill" button uses (§3.1:
  import produces the identical activate=owns outcome a manual create would).
- A new dialog: source picker (same-BU dropdown or an external-URL text field),
  then the same display-name/description/when-to-use/body fields
  `SkillEditorDialog`'s create mode already has — reusing `SkillEditorDialog`'s
  field components rather than building a parallel form. **No new endpoint for
  the dropdown**: `frontend/lib/api/workspaces.ts`'s existing `listWorkspaces()`
  (backing `GET /workspaces`) already exists — confirmed its backend filters to
  `allowed_workspace_ids` (VISIBLE units), which is broader than `administered_
  workspace_ids` (ADMINISTERED units, what the provenance screen actually
  requires) — a caller with only a project-level binding under a foreign BU
  could see that BU in the dropdown but would correctly be refused
  (`SOURCE_NOT_ALLOWED`) if they picked it. This is a UX rough edge, not a
  security gap (the backend check is authoritative and unaffected by what the
  dropdown shows); tightening the dropdown to administered-only units is a
  reasonable nice-to-have during planning if it's cheap, not a hard requirement.
- Screen failures surface via the SAME `getLintViolations`/toast pattern
  create/update already use for a 422 — `CREDENTIAL_DETECTED`/`SOURCE_NOT_
  ALLOWED` render as a clear, specific toast, not a generic error.

## 6. Explicitly out of scope (considered and rejected)

- Live external-source fetching (§3.4).
- A quarantine/manual-review staged state (§3.4).
- Behavior (prompt) import (§3.1).
- A per-BU-editable allowlist (considered and rejected — see §2's `OrgModelGrant`
  precedent; a BU Admin curating their own allowed sources is the exact inversion
  of governance that record's own docstring says was already tried and reverted).
- Retroactively scanning content that predates this sub-project (existing skills
  were never screened; this sub-project gates NEW imports going forward only).

## 7. Follow-up work this sub-project surfaces but does not do

- Real external-source integration (a GitHub App or similar), once product
  scope actually calls for live fetching rather than paste-and-declare.
- A quarantine/manual-security-review lifecycle, if refuse-outright proves too
  blunt in practice for borderline cases.
