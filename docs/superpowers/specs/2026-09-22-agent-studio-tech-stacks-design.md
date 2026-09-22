# Agent Studio tech stacks — design

**Date:** 2026-09-22 · **Status:** for review · **Branch:** `agent-studio-tech-stacks` (from `track-3-brief-and-agent-names`)

## 1. The problem

Agent Studio → Skills lets admins create skills, but a rule such as "design with our approved
stack" never reliably reaches what the agent produces:

1. **Skills are optional reading.** An agent's prompt lists skills by name; the model decides
   whether to call `load_skill`. A stack rule can be skipped entirely.
2. **The stack is chosen where skills never reach.** Design documents come from the
   `generate_architecture` tool, which makes its **own** model call with its own prompt
   (`components.build_generation_prompt`). Nothing a chat model loaded reaches that call.
3. **No notion of alternatives.** Scopes are "nearest wins per key"; there is no way for a
   Business Unit to offer several stacks and a project to pick one.
4. **Housekeeping:** the chat prompt lists skills only on a session's first turn; the Design
   agent's REST entry point resolves skills with no tenant; the dev database holds 28 leftover
   test skills on the Requirements agent.

## 2. Decisions (agreed 2026-09-22)

- A tech stack is **structured** (categories of items) **plus free-text notes**.
- **Business Unit admins** create stacks for their BU, as **alternatives**, and mark at most one
  as the **BU default**. **Project admins** see the BU's stacks, can read each, and switch the
  one their project uses, or create a project-only stack.
- **Exactly one stack at a time per project** (radio behaviour). The project's choice
  **replaces** the BU's; nothing is enforced upward.
- **Organisation admins are not a tier here** (no org-level stacks for now). As everywhere in
  Agent Studio, an org admin's `admin:*` still owns every tier.
- **One choice per project, shared by all agents.** The Design agent is wired now; Development,
  Testing and Deployment read the same choice in a follow-up.

## 3. Rules

**Tech stack.** `name`, optional `description`, `notes`, and items in fixed categories:
languages, backend frameworks, frontend frameworks, databases, cloud & hosting, messaging &
integration, CI/CD & DevOps, testing, observability. Items are free text, with suggestions from a
built-in catalogue. Limits: name 3–80 characters and unique (case-insensitive) among live stacks
of the same tier; description ≤ 280; notes ≤ 2,000; ≤ 20 items per category, each ≤ 60
characters, trimmed and de-duplicated; at least one item overall.

**Tiers.** A stack belongs to a **Business Unit** (owner: that BU's admin) or to a **project**
(owner: that project's admin). Ownership is `resolve_actor_tier_access(...).owns`, the exact rule
Agent Studio already uses, so **no new permissions or role rows**. That also means no RBAC
catalogue drift for other branches.

**The project's effective stack**, in order:
1. the project's selection, if it names a live stack that is the project's own, or belongs to
   the project's **current** BU;
2. otherwise the BU default;
3. otherwise **none**: agents behave exactly as today and recommend a stack freely.

The answer always says where it came from (`project selection` / `BU default` / `none`). If
rule 1 is skipped because the selected stack was deleted, or the project moved BU, the answer
carries a **warning**, which Agent Studio and the Design page show.

**Lifecycle.**
- **Edits** apply to the next generation.
- **Delete** is soft. A project that selected the deleted stack falls back to the BU default,
  with a warning.
- **Deleting the BU default** leaves the BU with no default.
- Every write is audited (`tech_stack.created|updated|deleted|default_set`,
  `project.tech_stack_selected`) through `audit_service.emit`, and invalidates the resolver
  cache.

## 4. Data model — migration `0065_tech_stacks`

- `tech_stacks`
  - `id` uuid pk; `tenant_id` uuid
  - `scope` (`workspace` | `project`, CHECK); `workspace_id` uuid null; `project_id` uuid null
    - CHECK: exactly the one matching the scope is set
  - `name` text; `description` text; `categories` jsonb (`{category: [items]}`); `notes` text
  - `is_default` bool (CHECK: true only when `scope = 'workspace'`)
  - `created_by`, `updated_by`, `created_at`, `updated_at`, `deleted_at`
  - Partial unique indexes:
    - one live default per workspace
    - live name unique per (scope, scope id), case-insensitive
- `project_tech_stack_selections`
  - `project_id` uuid pk; `tenant_id`; `tech_stack_id` uuid FK → `tech_stacks.id`
  - `selected_by`, `selected_at`
  - No row = "follow the BU default".
- Both tables: tenant RLS (`tenant_isolation` + insert policy, `FORCE`) and `sdlc_app` grants,
  as in `0034`.
- Tables only: no role or permission rows, no changes to existing tables.

## 5. Backend

**`shared/services/tech_stack.py`**
- validation and lint
- `resolve_project_tech_stack(tenant_id, project_id) -> EffectiveTechStack | None`, with a 45 s
  TTL cache like `resolve_skills_cached`, plus `invalidate_tech_stack_cache`
- `render_for_prompt(stack) -> str`
- `check_stack_table(stack, markdown) -> list[Violation]`

**`shared/services/tech_stack_catalog.py`:** categories, labels and suggestion items. One source
for the UI's suggestions.

**`shared/routers/tech_stacks.py`**
- Floor: `artifact:view`, like `agent_skills`. The BU tier is readable by tenant members (shared
  tiers are readable, as in Agent Studio). Project reads also require project visibility
  (`visible_project_ids`).

| Route | Who | Does |
|---|---|---|
| `GET /tech-stacks/catalog` | any member | categories + suggestions |
| `GET /tech-stacks?workspace_id=` | any member | the BU's live stacks, default first |
| `GET /projects/{id}/tech-stack` | project visible | options (BU stacks + project stacks), selection, effective stack, warnings, `canManage` |
| `POST /tech-stacks` | owner of the target tier | create (`scope`, `scope_id`, fields) |
| `PATCH /tech-stacks/{id}` | owner of the stack's tier | edit |
| `DELETE /tech-stacks/{id}` | owner of the stack's tier | soft delete |
| `PUT /tech-stacks/{id}/default` | BU owner | set or clear the BU default (one per BU) |
| `PUT /projects/{id}/tech-stack` | project owner | `{techStackId}` or `null` (= follow BU default); the stack must be live and the project's own or its current BU's |

- Errors are explicit:
  - 422 with `violations` (the same shape Agent Studio already renders)
  - 403 for a non-owner, 404 for a missing or other-tenant stack
  - 409 for a stale default or a stack from another BU

**Agent runtime (Design agent now)**
- `current_project_tech_stack()` reads the turn's tenant and project from `config.ws_helper`,
  which the Design APIs already set, and resolves them through the cache.
- **Chat model, every turn:** the design node prepends a "PROJECT TECH STACK — mandatory"
  system note to the messages it sends. The note is not stored in the chat state, so it is
  always current.
- **The generation tool** (`_generate_components` → `build_generation_prompt(..., tech_stack=)`):
  - The mandatory block goes into the prompt of every generated section, because C4 containers,
    LLD, schema and ADRs also name technologies.
  - The rule: use only the stack's technologies. Where the stack does not cover a need, write
    "Not covered by the project's tech stack — needs a decision" instead of introducing another
    technology.
  - Also applied to `update_response` edits.
- **Conformance, deterministic:**
  1. After generating the Technology Stack section, each row's Technology cell is checked
     against the stack's items (case-insensitive containment; "not covered" markers pass).
  2. On any violation, that section is regenerated once, with the violations named.
  3. Anything still outside the stack is kept in a visible callout under the section header, and
     reported in the tool's result for the agent to relay. Nothing is dropped silently.
- **Traceability:** the Technology Stack section opens with
  `Project tech stack: **<name>** (<source>)`, inserted deterministically.
- **Side fix:** the Design REST entry point passes its real tenant when resolving skills and
  profile.

## 6. Frontend

**Agent Studio → Skills tab:** a **"Tech stack · all agents"** panel sits above the agent's
skill list. The same panel appears on every agent, from one query.
- **Organization tier:** a short note: stacks are set per Business Unit and chosen per project.
- **Business Unit tier:**
  - The BU's stacks as cards (name, description, category chips, "Default" badge).
  - The owner can create, edit and delete stacks, and set or clear the default.
  - Everyone else sees them read-only.
- **Project tier:**
  - A radio group: "Follow BU default (*name*)", each BU stack, and each project stack.
  - "View" opens a stack's full contents.
  - The owner switches the selection (saved immediately, with a toast) and can create, edit or
    delete project stacks.
  - Others see it read-only.
  - Warnings (deleted selection, moved BU) are shown as a callout.
- **Personal tier:** the project's effective stack, read-only.
- **Editor dialog:** name, description, a chip input per category (type-ahead suggestions from the
  catalogue, free entry allowed), and notes. Server `violations` are shown on their fields, as the
  Skills editor does.

**Design page header:** a chip, "Tech stack: *name*" or "No tech stack set". It links to Agent
Studio at the project tier, and shows the warning when there is one.

**Plumbing:** `lib/api/tech-stacks.ts` (zod schemas), `qk.techStacks.*`, BFF routes mirroring
the agent-skills ones, and invalidation of the project and BU queries after every write.

## 7. Testing

- **Backend unit:**
  - resolver order (selection → default → none), deleted and other-BU fallbacks with warnings
  - validation limits, name uniqueness
  - route authorisation (owner / non-owner / other BU) through the ownership helper
  - default exclusivity
  - `render_for_prompt`; `check_stack_table` cases (allowed, violation, "not covered")
  - the generation prompt carries the block (a captured model call); one corrective regeneration
    on violation
  - the chat note is prepended without entering the state
- **Frontend:** the panel at each tier (owner vs read-only), switching the radio, the editor's
  validation display, the Design page chip.
- **Live (dev DB, real model — xAI grok-3-mini):**
  1. Create three stacks on QuickLink's BU: Java + Spring Boot, Node + Next.js,
     Python + FastAPI.
  2. Generate the Technology Stack section for QuickLink from the same approved BRD, once per
     stack. Each result must use only its stack's technologies, and the three must differ.
  3. Clear the selection and check that the BU default applies.
  4. Delete the selected stack and check the fallback, with its warning.
  5. Report the three stack tables side by side.

## 8. Rollout

- Migration `0065` only adds two new tables, so no existing data changes; its downgrade drops
  them. It is applied to the dev DB (`localhost:5433/sdlc_product`) the same way as 0064: check
  the revision, run alembic from the venv, verify, and restart the backend once. The test DB is
  noted, not migrated.
- The three demo stacks created by the live test stay in the dev DB for demos, clearly named.

## 9. Out of scope (follow-ups)

- Development, Testing and Deployment agents consuming the project stack, and the Orchestrator.
- An organisation tier; a propose/approve flow for stacks.
- Removing the 28 leftover test skills from the dev DB. They're destructive to delete, so this
  needs a separate go-ahead.
