# Agent Studio 5: Import + supply-chain screening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Business Unit Admin can import a Skill from another BU they administer,
or from an external source they declare, through three screens (prompt-injection,
credential leakage, provenance/allowlist) before it lands via the exact same
`create_custom_skill`/`propose_skill` path an ordinary create already uses.

**Architecture:** `POST /agent-skills/import` runs the same authorization
(`resolve_actor_tier_access`), the same lint (`lint_skill_fields`, reusing
`FORBIDDEN_PATTERNS`), and the same duplicate-key/`create_custom_skill(activate=
owns)` sequence `create_skill` already runs — with two new screens inserted before
lint: a credential scan (`shared/eval/import_screening.py`'s `scan_for_credentials`,
reusing `_SECRET_PATTERNS`' compiled regexes from `sandbox_policy.py`) and a
provenance check against either `administered_workspace_ids` (same-tenant-BU
source) or a new, Org-Admin-only-writable `import_source_allowlist` table
(external source), mirroring `OrgModelGrant`'s "the Org Admin governs the
catalogue" governance shape. No new governance request type — a non-owner's
import proposes through the existing `agent_default_*` flow exactly like a normal
non-owner create does.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic migration, Next.js + React
Query + Zod frontend, pytest (backend, incl. live-DB tests), vitest + React
Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-08-26-agent-studio-5-import-supply-chain-screening-design.md`

## Global Constraints

- Import is Skills-only. Do not touch `agent_profiles.py`/Behavior at all in this
  plan (spec §3.1/§4).
- No live external fetching — "external source" means the importer pastes content
  and declares a source URL string, checked against the allowlist by prefix match
  (`source_pattern` is a URL/domain PREFIX; a declared source matches if it starts
  with an allowlisted pattern — plain Python `str.startswith`, not a regex, so an
  admin-typed pattern can never become a regex-injection surface).
- No quarantine/staged-for-review state. Each screen either passes or the import
  is refused outright (422) before anything is written — no partial writes, no new
  lifecycle state (spec §3.4).
- No new governance request type. A non-owner's import reuses `agent_default_org`/
  `_workspace`/`_project` exactly like `propose_skill()` already does — this plan
  does not touch `governance_requests.py`, `effects.py`, or `routing.py` at all.
- `import_source_allowlist` writes require the `admin:*` wildcard (Org Admin) —
  reuse the existing `is_org_wide`/`ORG_WIDE_PERMISSIONS` check from
  `shared/authz/read_scope.py`, do not invent a new permission string.
- The credential screen only runs the DETECTION half of `_SECRET_PATTERNS`
  (the compiled `re.Pattern` — ignore the redaction-replacement string, the second
  tuple element) — do not import or depend on `sanitize_output`/`SandboxPolicy`
  from `sandbox_policy.py`; that module's job (redacting agent tool output) is a
  different concern from refusing an import outright.
- Follow this repo's established RLS/tenant-scoping conventions for the new table
  (mirror `agent_default_evaluations`' migration from sub-project 4 — `ENABLE`/
  `FORCE ROW LEVEL SECURITY`, the same `tenant_isolation`/`tenant_isolation_insert`
  policy predicates, written via `get_db_session_for_tenant`).

---

### Task 1: Migration + ORM model — `import_source_allowlist`

**Files:**
- Modify: `backend/shared/models/orm.py` (add `ImportSourceAllowlist`, near `AgentDefaultEvaluation`)
- Create: `backend/migrations/versions/<next_revision>_import_source_allowlist.py`
- Test: `backend/tests/test_import_source_allowlist_schema.py` (new)

**Interfaces:**
- Produces: `ImportSourceAllowlist` ORM class — columns `id, tenant_id,
  source_pattern, label, created_by, created_at`. Used by Task 2's provenance
  check and Task 4's allowlist-management routes.

- [ ] **Step 1: Add the ORM model**

Read `AgentDefaultEvaluation` in `backend/shared/models/orm.py` first (the most
recent precedent for a new RLS table added in this branch) and match its exact
migration/grant/RLS shape. Add, near it:

```python
class ImportSourceAllowlist(Base):
    """Org-Admin-governed allowlist of approved external import sources
    (Agent Studio sub-project 5). A declared import source matches if it starts
    with one of these patterns (plain prefix match, never a regex — an
    admin-typed pattern must never become a regex-injection surface). Mirrors
    OrgModelGrant's "the Org Admin governs the catalogue" doctrine: a BU Admin
    cannot self-approve their own import source, the same way they cannot
    self-grant a model. Tenant-scoped under FORCE RLS, mirroring
    AgentDefaultEvaluation.
    """
    __tablename__ = "import_source_allowlist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`UUID`, `String`, `DateTime`, `func`, `Mapped`, `mapped_column` are all already
imported in this file (confirm by grep before assuming, matching this branch's
established caution here) — no new imports needed.

- [ ] **Step 2: Write the migration**

Run `cd backend && alembic heads` for the current head, then create
`backend/migrations/versions/<head>_import_source_allowlist.py`. Copy
`0034_agent_default_evaluations.py`'s exact `upgrade()`/`downgrade()` shape:
`op.create_table`, an index on `tenant_id`, `ENABLE ROW LEVEL SECURITY` +
`tenant_isolation` (USING) + `tenant_isolation_insert` (WITH CHECK) +
`FORCE ROW LEVEL SECURITY`, then the conditional `sdlc_app` grant block. Grant
`SELECT, INSERT` only — this table is append-only from the API's point of view
too (no UPDATE/DELETE route is planned; an Org Admin who wants to remove an entry
gets that as explicit follow-up work, not this task's job) — mirror `0034`'s
POST-FIX grant shape (`GRANT SELECT, INSERT` + explicit `REVOKE UPDATE, DELETE`),
not its original pre-fix version.

- [ ] **Step 3: Write the schema test**

```python
import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant


@pytest.mark.asyncio
async def test_import_source_allowlist_round_trips():
    tenant = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text(
                "INSERT INTO import_source_allowlist "
                "(id, tenant_id, source_pattern, label, created_by) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :p, :l, :cb)"
            ),
            {"id": row_id, "t": tenant, "p": "https://github.com/acme-org/", "l": "Acme skill library", "cb": "user-1"},
        )
        row = (
            await s.execute(
                text("SELECT source_pattern, label FROM import_source_allowlist WHERE id = CAST(:id AS uuid)"),
                {"id": row_id},
            )
        ).first()
    assert row is not None
    assert row.source_pattern == "https://github.com/acme-org/"
    assert row.label == "Acme skill library"
```

- [ ] **Step 4: Run the migration and the test**

Run: `cd backend && alembic upgrade head`
Then: `./.venv/Scripts/python.exe -m pytest tests/test_import_source_allowlist_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/models/orm.py backend/migrations/versions/ backend/tests/test_import_source_allowlist_schema.py
git commit -m "feat: add import_source_allowlist table for Agent Studio import screening"
```

---

### Task 2: Credential screen — `shared/eval/import_screening.py`

**Files:**
- Create: `backend/shared/eval/import_screening.py`
- Test: `backend/tests/eval/test_import_screening.py` (new)

**Interfaces:**
- Consumes: `_SECRET_PATTERNS` from
  `agents_orchestrator/development_agent/tools/sandbox_policy.py` (existing,
  DETECTION half only — see Global Constraints).
- Produces: `scan_for_credentials(text: str) -> list[str]` — a list of matched
  pattern CATEGORY NAMES (never the matched text itself), empty if clean. Used by
  Task 3's import route.

- [ ] **Step 1: Write the failing tests**

```python
from shared.eval.import_screening import scan_for_credentials


def test_clean_text_returns_empty():
    assert scan_for_credentials("Cover acceptance criteria and scope.") == []


def test_github_pat_detected():
    hits = scan_for_credentials("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert hits


def test_bearer_token_detected():
    hits = scan_for_credentials("Authorization: Bearer abcdefghijklmnop")
    assert hits


def test_never_echoes_the_matched_secret():
    hits = scan_for_credentials("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    joined = " ".join(hits)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in joined


def test_multiple_categories_all_reported():
    hits = scan_for_credentials(
        "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
        "Authorization: Bearer abcdefghijklmnop"
    )
    assert len(hits) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/eval/test_import_screening.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Read `_SECRET_PATTERNS`, then write the implementation**

Read `_SECRET_PATTERNS` in `backend/agents_orchestrator/development_agent/tools/
sandbox_policy.py` first (confirms the exact 5 entries and their shape:
`list[tuple[re.Pattern, str]]` — pattern, redaction-replacement) before writing
this. Give each entry a short, human-readable CATEGORY NAME for the hit list
(e.g. "password/secret assignment", "Bearer token", "GitHub personal access
token", "API key (OpenAI/Anthropic-shaped)", "credentials in a URL") — write
these names as a parallel list here, not by modifying `sandbox_policy.py` (that
module's shape/order is a stable, tested contract another feature depends on;
this task must not touch it).

```python
"""Credential-leakage detection for Agent Studio imports (sub-project 5).

Reuses the DETECTION half of shared/agents_orchestrator/development_agent/tools/
sandbox_policy.py's _SECRET_PATTERNS — the same regexes that redact agent tool
output — but for a different purpose: refusing an import outright rather than
redacting and continuing. Never imports sanitize_output/SandboxPolicy; that
module's redaction behavior is a different concern from this one's refuse-or-pass
decision.
"""
from __future__ import annotations

from agents_orchestrator.development_agent.tools.sandbox_policy import _SECRET_PATTERNS

# One label per _SECRET_PATTERNS entry, same order — never echoes the matched
# text itself, only which category matched (an error message that echoed a
# leaked secret back to the caller would defeat the point of catching it).
_CATEGORY_LABELS: list[str] = [
    "password/secret assignment",
    "Bearer token",
    "GitHub personal access token",
    "API key (OpenAI/Anthropic-shaped)",
    "credentials embedded in a URL",
]


def scan_for_credentials(text: str) -> list[str]:
    """Category names of every _SECRET_PATTERNS entry that matches `text`, in
    _SECRET_PATTERNS' own order. Empty list = clean. Never returns the matched
    text itself."""
    hits: list[str] = []
    for (pattern, _replacement), label in zip(_SECRET_PATTERNS, _CATEGORY_LABELS):
        if pattern.search(text or ""):
            hits.append(label)
    return hits
```

If `_SECRET_PATTERNS` has a different number of entries than 5 when you actually
read it, adjust `_CATEGORY_LABELS` to match exactly — `zip()` silently truncates
to the shorter list, so a mismatch here would silently under-report categories
rather than crash; get the count right by reading the real file, not by trusting
this brief's count.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/eval/test_import_screening.py -v`
Expected: PASS. Adjust the GitHub-PAT/Bearer-token test fixtures if the real
regexes' length/character requirements don't match what's written here (re-read
`_SECRET_PATTERNS`' exact patterns first).

- [ ] **Step 5: Commit**

```bash
git add backend/shared/eval/import_screening.py backend/tests/eval/test_import_screening.py
git commit -m "feat: credential-leakage screen for Agent Studio imports"
```

---

### Task 3: Backend — `POST /agent-skills/import`

**Files:**
- Modify: `backend/shared/routers/agent_skills.py` (new `import_skill` route)
- Test: `backend/tests/agent_skills/test_import.py` (new)

**Interfaces:**
- Consumes: `scan_for_credentials` (Task 2), `ImportSourceAllowlist` (Task 1),
  `administered_workspace_ids` (existing, `shared/authz/read_scope.py`),
  `resolve_actor_tier_access`, `lint_skill_fields`, `create_custom_skill`
  (all existing).
- Produces: `POST /agent-skills/import`.

- [ ] **Step 1: Write the failing tests**

```python
import uuid

import httpx
import pytest

from process_api import app
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from sqlalchemy import text


async def _bind_role(tenant_id, user_id, role, scope_kind, scope_id):
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


@pytest.mark.asyncio
async def test_import_from_administered_bu_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    source_ws = str(uuid.uuid4())
    target_ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", source_ws)
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", target_ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": target_ws,
                "skill_key": "imported-skill", "display_name": "Imported Skill",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "same_tenant_bu", "workspace_id": source_ws},
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["skill_key"] == "imported-skill"


@pytest.mark.asyncio
async def test_import_from_non_administered_bu_refused(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    target_ws = str(uuid.uuid4())
    not_administered_ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", target_ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": target_ws,
                "skill_key": "imported-skill-2", "display_name": "Imported",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "same_tenant_bu", "workspace_id": not_administered_ws},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SOURCE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_import_with_credential_in_body_refused(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "leaky-skill", "display_name": "Leaky",
                "description": "d", "when_to_use": "w",
                "body": "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                "source": {"kind": "same_tenant_bu", "workspace_id": ws},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "CREDENTIAL_DETECTED"


@pytest.mark.asyncio
async def test_import_from_unlisted_external_source_refused(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill", "display_name": "External",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://untrusted.example.com/skill.md"},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SOURCE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_import_from_allowlisted_external_source_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO import_source_allowlist (id, tenant_id, source_pattern, label, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :p, 'Trusted', 'org-admin-1')"
        ), {"t": tenant, "p": "https://trusted.example.com/"})
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill-2", "display_name": "External Trusted",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://trusted.example.com/skills/foo.md"},
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/agent_skills/test_import.py -v`
Expected: FAIL — route doesn't exist (404).

- [ ] **Step 3: Read `create_skill`, then write the implementation**

Read `create_skill` in `backend/shared/routers/agent_skills.py` in full first —
this route is a near-copy of it with two screens inserted before the lint call.
Reuse its imports (`resolve_actor_tier_access`, `lint_skill_fields`,
`validate_skill_key`, `get_vendor_skill`) — do not re-import anything already
imported in this file.

```python
class ImportSourceIn(BaseModel):
    kind: str  # "same_tenant_bu" | "external"
    workspace_id: Optional[str] = None
    url: Optional[str] = None


class ImportSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
    skill_key: str
    display_name: str
    description: Optional[str] = None
    when_to_use: Optional[str] = None
    body: str
    source: ImportSourceIn


@agent_skills_router.post("/import")
async def import_skill(body: ImportSkillIn, request: Request):
    """Bring in Skill content from another BU the caller administers, or a
    declared external source, through three screens before it lands via the
    SAME create_custom_skill path an ordinary create already uses — import is
    a different front door onto the existing write path, not a parallel one
    (sub-project 5 spec §3.1). See create_skill for the sequence this mirrors.
    """
    from shared.authz.read_scope import administered_workspace_ids  # noqa: PLC0415
    from shared.eval.import_screening import scan_for_credentials  # noqa: PLC0415
    from sqlalchemy import select as _select  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import ImportSourceAllowlist  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
    if body.scope == "user":
        owns = True
    else:
        owns, _ = await resolve_actor_tier_access(tenant_id, _user_id(request), perms, body.scope, body.scope_id)

    # ── Screen 3: provenance ──────────────────────────────────────────────
    if body.source.kind == "same_tenant_bu":
        if not body.source.workspace_id:
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "A same-BU import must name the source workspace.",
            })
        async with get_db_session_for_tenant(tenant_id) as session:
            administered = await administered_workspace_ids(session, request)
        if administered is not None and body.source.workspace_id not in administered:
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "You do not administer the source business unit.",
            })
    elif body.source.kind == "external":
        url = (body.source.url or "").strip()
        if not url:
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "An external import must declare a source URL.",
            })
        async with get_db_session_for_tenant(tenant_id) as session:
            rows = (await session.execute(
                _select(ImportSourceAllowlist.source_pattern).where(
                    ImportSourceAllowlist.tenant_id == tenant_id,
                )
            )).scalars().all()
        if not any(url.startswith(p) for p in rows):
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "This source is not on the organization's approved import list.",
            })
    else:
        raise HTTPException(status_code=422, detail={
            "code": "SOURCE_NOT_ALLOWED",
            "message": "source.kind must be 'same_tenant_bu' or 'external'.",
        })

    # ── Screen 2: credential leakage ──────────────────────────────────────
    credential_hits = scan_for_credentials(
        "\n".join(filter(None, [body.display_name, body.description, body.when_to_use, body.body]))
    )
    if credential_hits:
        raise HTTPException(status_code=422, detail={
            "code": "CREDENTIAL_DETECTED",
            "message": f"Possible credential detected ({', '.join(credential_hits)}); remove it before importing.",
        })

    # ── Screen 1: prompt-injection (same lint create_skill already runs) ──
    violations = validate_skill_key(body.skill_key) + lint_skill_fields(
        body.display_name, body.description, body.when_to_use, body.body
    )
    if violations:
        raise HTTPException(status_code=422, detail={"violations": violations})

    store = _store()
    vendor_hit = get_vendor_skill(body.agent_id, body.skill_key) is not None
    custom_hit = await store.get_skill_detail(
        tenant_id, body.agent_id, body.scope, body.scope_id, "custom", body.skill_key
    ) is not None
    if vendor_hit or custom_hit:
        raise HTTPException(status_code=422, detail={"violations": [{
            "field": "skill_key", "code": "duplicate_key",
            "message": f"skill_key '{body.skill_key}' already exists for this agent.",
        }]})

    try:
        detail = await store.create_custom_skill(
            tenant_id, body.agent_id, body.scope, body.scope_id, body.skill_key,
            body.display_name, body.description, body.when_to_use, body.body,
            _user_id(request) or "system", activate=owns,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail={"violations": [{
            "field": "skill_key", "code": "duplicate_key",
            "message": f"skill_key '{body.skill_key}' already exists for this agent.",
        }]})
    _runtime().invalidate_skills_cache(tenant_id, body.agent_id)
    await _emit(request, tenant_id, "skill.imported", body.skill_key, {
        "agent_id": body.agent_id, "scope": body.scope, "scope_id": body.scope_id,
        "skill_key": body.skill_key, "origin": "custom", "source_kind": body.source.kind,
    })
    return detail
```

`administered_workspace_ids(db, request)` is confirmed against the real function
in `shared/authz/read_scope.py:152` — it takes `(db: AsyncSession, request:
Request)` and returns `None` for an org-wide caller (meaning "the whole
organization," so an org_admin's same-BU import always passes this leg) or a
list of workspace ids otherwise. The code above already opens its own session
via `get_db_session_for_tenant` to call it, matching the `external` branch's
existing pattern in the same route — no further verification needed.

Also place this route BEFORE `POST ""` (`create_skill`) or confirm route ordering
doesn't matter here — `/import` is a literal path segment at the same level as
`/toggle`, so it needs no special ordering relative to the `{skill_key}`-prefixed
routes (mirror where `/toggle` is declared, in the "Literal-suffix routes"
section per this file's own `ROUTE ORDER` docstring comment).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/agent_skills/test_import.py tests/agent_skills -v`
Expected: ALL PASS, including every pre-existing test in `tests/agent_skills/`.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_skills.py backend/tests/agent_skills/test_import.py
git commit -m "feat: POST /agent-skills/import with prompt-injection, credential, and provenance screens"
```

---

### Task 4: Backend — allowlist management routes (Org Admin)

**Files:**
- Modify: `backend/shared/routers/agent_skills.py` (new `GET`/`POST /agent-skills/import-sources` routes)
- Test: `backend/tests/agent_skills/test_import_sources.py` (new)

**Interfaces:**
- Produces: `GET /agent-skills/import-sources` (router floor only — any tenant
  member may see the list, matching "a BU Admin needs to see the list to know
  what they may declare" from the spec), `POST /agent-skills/import-sources`
  (Org Admin only, via `is_org_wide`).

- [ ] **Step 1: Write the failing tests**

```python
import uuid

import httpx
import pytest

from process_api import app
from shared.db import get_db_session_for_tenant
from sqlalchemy import text


async def _bind_role(tenant_id, user_id, role, scope_kind, scope_id):
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


@pytest.mark.asyncio
async def test_org_admin_can_add_an_allowlist_entry(mint_token):
    tenant = str(uuid.uuid4())
    org_admin_id = str(uuid.uuid4())
    token = mint_token(user_id=org_admin_id, tenant_id=tenant, permissions=["artifact:view", "admin:*"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills/import-sources",
            json={"source_pattern": "https://github.com/acme-org/", "label": "Acme skill library"},
            headers=headers,
        )
        assert created.status_code == 201, created.text

        listed = await client.get("/agent-skills/import-sources", headers=headers)
        assert listed.status_code == 200
        assert any(e["source_pattern"] == "https://github.com/acme-org/" for e in listed.json()["sources"])


@pytest.mark.asyncio
async def test_non_org_admin_cannot_add_an_allowlist_entry(mint_token):
    tenant = str(uuid.uuid4())
    bu_admin_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, bu_admin_id, "bu_admin", "business_unit", ws_id)
    token = mint_token(user_id=bu_admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import-sources",
            json={"source_pattern": "https://evil.example.com/", "label": "Self-approved"},
            headers=headers,
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_any_member_can_list_allowlist_entries(mint_token):
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, dev_id, "developer", "project", project_id)
    token = mint_token(user_id=dev_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agent-skills/import-sources", headers=headers)
        assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/agent_skills/test_import_sources.py -v`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Write the implementation**

Read `is_org_wide`/`ORG_WIDE_PERMISSIONS` in `shared/authz/read_scope.py` first
(confirm the exact function signature — it takes a `Request`, reads
`request.state.permissions`) before using it.

```python
class ImportSourceCreateIn(BaseModel):
    source_pattern: str
    label: str


@agent_skills_router.get("/import-sources")
async def list_import_sources(request: Request):
    from sqlalchemy import select as _select  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import ImportSourceAllowlist  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    async with get_db_session_for_tenant(tenant_id) as session:
        rows = (await session.execute(
            _select(ImportSourceAllowlist).where(
                ImportSourceAllowlist.tenant_id == tenant_id,
            ).order_by(ImportSourceAllowlist.label)
        )).scalars().all()
    return {"sources": [
        {"id": str(r.id), "source_pattern": r.source_pattern, "label": r.label,
         "created_by": r.created_by, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


@agent_skills_router.post("/import-sources", status_code=201)
async def create_import_source(body: ImportSourceCreateIn, request: Request):
    from shared.authz.read_scope import is_org_wide  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import ImportSourceAllowlist  # noqa: PLC0415

    if not is_org_wide(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    tenant_id = _tenant_id(request)
    pattern = body.source_pattern.strip()
    if not pattern:
        raise HTTPException(status_code=422, detail="source_pattern must not be empty")

    async with get_db_session_for_tenant(tenant_id) as session:
        row = ImportSourceAllowlist(
            tenant_id=tenant_id, source_pattern=pattern, label=body.label,
            created_by=_user_id(request) or "system",
        )
        session.add(row)
        await session.flush()
        return {"id": str(row.id), "source_pattern": row.source_pattern, "label": row.label}
```

Place both routes in the "Literal-suffix routes" section of this file, alongside
`/import` from Task 3 and `/toggle` — same reasoning as Task 3's route-order note.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/agent_skills/test_import_sources.py tests/agent_skills -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_skills.py backend/tests/agent_skills/test_import_sources.py
git commit -m "feat: Org-Admin-only import-source allowlist management routes"
```

---

### Task 5: Frontend — schemas + API clients

**Files:**
- Modify: `frontend/lib/schemas/agent-skills.ts` (add `ImportSkillInput`, `ImportSourceEntry`)
- Modify: `frontend/lib/api/agent-skills.ts` (add `importAgentSkill`, `listImportSources`, `createImportSource`)
- Create: `frontend/app/api/agent-skills/import/route.ts`
- Create: `frontend/app/api/agent-skills/import-sources/route.ts`

**Interfaces:**
- Produces: `ImportSkillInput` Zod schema; `importAgentSkill(input) =>
  Promise<SkillDetail>`; `ImportSourceEntry` Zod schema; `listImportSources() =>
  Promise<{sources: ImportSourceEntry[]}>`; `createImportSource(input) =>
  Promise<{id, source_pattern, label}>`. Used by Task 6/7.

- [ ] **Step 1: Write the schemas**

In `frontend/lib/schemas/agent-skills.ts`, add (near `SkillCreateInput`):

```typescript
export const ImportSourceInput = z.object({
  kind: z.enum(["same_tenant_bu", "external"]),
  workspace_id: z.string().nullish(),
  url: z.string().nullish(),
});
export type ImportSourceInput = z.infer<typeof ImportSourceInput>;

export const ImportSkillInput = z.object({
  agent_id: z.string(),
  scope: SkillScope,
  scope_id: z.string().nullish(),
  skill_key: z.string(),
  display_name: z.string(),
  description: z.string().optional(),
  when_to_use: z.string().optional(),
  body: z.string(),
  source: ImportSourceInput,
});
export type ImportSkillInput = z.infer<typeof ImportSkillInput>;

export const ImportSourceEntry = z.object({
  id: z.string(),
  source_pattern: z.string(),
  label: z.string(),
  created_by: z.string().nullable(),
  created_at: z.string().nullable(),
});
export type ImportSourceEntry = z.infer<typeof ImportSourceEntry>;

export const ImportSourceList = z.object({
  sources: z.array(ImportSourceEntry),
});
export type ImportSourceList = z.infer<typeof ImportSourceList>;
```

- [ ] **Step 2: Add the API client functions**

In `frontend/lib/api/agent-skills.ts`, add:

```typescript
import {
  ImportSkillInput, ImportSourceEntry, ImportSourceList,
} from "@/lib/schemas/agent-skills";

/** Import a Skill from another BU the caller administers, or a declared
 *  external source, through the backend's prompt-injection/credential/
 *  provenance screens before it lands via the same path a create would. */
export const importAgentSkill = (input: ImportSkillInput) =>
  api("/agent-skills/import", { method: "POST", body: input, schema: SkillDetail });

/** The org's approved external import sources — readable by anyone, writable
 *  only by an Org Admin (POST). */
export const listImportSources = () =>
  api("/agent-skills/import-sources", { schema: ImportSourceList });

export const createImportSource = (input: { source_pattern: string; label: string }) =>
  api("/agent-skills/import-sources", {
    method: "POST", body: input,
    schema: ImportSourceEntry.pick({ id: true, source_pattern: true, label: true }),
  });
```

Match this file's existing import style (check whether other functions import
their input/output types the same way before finalizing).

- [ ] **Step 3: BFF proxy routes**

```typescript
// frontend/app/api/agent-skills/import/route.ts
import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { SkillDetail } from "@/lib/schemas/agent-skills";

/** Mirrors the create route's raw-body 422 passthrough (lint violations,
 *  CREDENTIAL_DETECTED, SOURCE_NOT_ALLOWED all need their real detail shape,
 *  not the generic ApiError envelope). */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  try {
    const data = await bffFetch("/agent-skills/import", { session, method: "POST", body, schema: SkillDetail });
    return Response.json(data);
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.rawBody ?? err.details ?? { code: err.code, message: err.message }, { status: err.status });
    }
    throw err;
  }
}
```

```typescript
// frontend/app/api/agent-skills/import-sources/route.ts
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ImportSourceEntry, ImportSourceList } from "@/lib/schemas/agent-skills";

export async function GET() {
  return bffProxy("/agent-skills/import-sources", { schema: ImportSourceList });
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/agent-skills/import-sources", {
    method: "POST", body,
    schema: ImportSourceEntry.pick({ id: true, source_pattern: true, label: true }),
  });
}
```

Read `frontend/app/api/agent-skills/route.ts` first for this file's established
GET/POST-in-one-file pattern and raw-422-passthrough convention (already used
there for create's lint violations) before finalizing either new route file.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/schemas/agent-skills.ts frontend/lib/api/agent-skills.ts frontend/app/api/agent-skills/import frontend/app/api/agent-skills/import-sources
git commit -m "feat: frontend schema + API clients for Agent Studio import"
```

---

### Task 6: Frontend — `skills-tab.tsx` Import action

**Files:**
- Modify: `frontend/components/agent-studio/skills-tab.tsx`
- Test: `frontend/components/agent-studio/__tests__/skills-tab.test.tsx` (existing — add cases)

**Interfaces:**
- Consumes: `importAgentSkill`, `listImportSources` (Task 5).

- [ ] **Step 1: Read the current file**

Read the current "New skill" button (both the header and empty-state instances,
gated on `canManage && !authoringDisabled`) and `SkillEditorDialog`'s create-mode
field layout (display name, skill key, description, when-to-use, body) — the
Import dialog reuses those same field components, not a parallel form.

- [ ] **Step 2: Write the failing test**

```typescript
it("shows an Import action for a manager, and calls the API with the declared source on submit", async () => {
  const mockedImport = vi.mocked(importAgentSkill);
  mockedImport.mockResolvedValue({
    origin: "custom", skill_key: "imported", agent_id: "requirements",
    display_name: "Imported", description: null, when_to_use: null,
    runtime: "llm", enabled: true, editable: true, deletable: true,
    version: 1, active_version: 1, origin_scope: "project",
    body: "x", created_by: "user-1", created_at: null, updated_at: null,
  });
  mockedListAgentSkills.mockResolvedValue({ skills: [] });

  const user = userEvent.setup();
  renderSkillsTab(projectScopeContext(true));

  await user.click(screen.getByRole("button", { name: /import/i }));
  await user.type(screen.getByLabelText("Display name"), "Imported");
  await user.type(screen.getByLabelText("Skill key"), "imported");
  await user.type(screen.getByLabelText(/instructions/i), "x");
  // Source picker defaults to "same_tenant_bu" — fill whatever the dialog's
  // default source fields require to submit; adjust to the actual field
  // labels once the dialog is implemented.
  await user.click(screen.getByRole("button", { name: /^import$/i }));

  await waitFor(() => expect(mockedImport).toHaveBeenCalled());
});
```

This is a starting sketch, not a literal final test — the exact source-picker
field labels depend on how you implement Step 3 below; update this test's
selectors to match once the dialog exists, keeping the same intent (Import
button visible, dialog collects source + content, submit calls the real API).

- [ ] **Step 3: Implement**

Add an "Import" button next to "New skill" (both instances), same
`canManage || canPropose` gate as `New skill` uses (spec §5). Add a new dialog
component (`SkillImportDialog`, sibling to `SkillEditorDialog`) with: a source
kind toggle (same-BU dropdown via the existing workspace-listing client — check
`frontend/lib/api/workspaces.ts` for its exact export name and shape before
wiring it up — or an external-URL text field), then the SAME
display-name/skill-key/description/when-to-use/body fields
`SkillEditorDialog`'s create mode already renders (reuse its field sub-
components directly rather than re-declaring them). On submit, call
`importAgentSkill` with the assembled `ImportSkillInput`; on success, close the
dialog and `invalidate()` the skills list (same as `SkillEditorDialog`'s
`onSaved`). On a `CREDENTIAL_DETECTED`/`SOURCE_NOT_ALLOWED` 422, show the
message from the error body as a toast (mirror how `getLintViolations`/its
sibling error-toast pattern already surfaces a 422's message elsewhere in this
file) rather than a generic "Couldn't import" message — the whole point of a
named error code is a specific, actionable message.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm test -- --run skills-tab`
Expected: PASS (after adjusting Step 2's sketch to match your actual field
labels).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/agent-studio/skills-tab.tsx frontend/components/agent-studio/__tests__/skills-tab.test.tsx
git commit -m "feat: Skills tab gains an Import action with source + screening feedback"
```

---

### Task 7: Frontend — Org Admin allowlist management UI

**Files:**
- Create: `frontend/components/agent-studio/import-source-allowlist.tsx`
- Modify: wherever Agent Studio's org-level settings/admin surface already
  lives (find it first — check `frontend/app/(app)/agent-studio/` or similar for
  an existing settings page/tab to add this to, rather than inventing a new
  standalone route; if genuinely nothing suitable exists, a new minimal page is
  acceptable but should be the last resort)
- Test: `frontend/components/agent-studio/__tests__/import-source-allowlist.test.tsx` (new)

**Interfaces:**
- Consumes: `listImportSources`, `createImportSource` (Task 5).

- [ ] **Step 1: Find the right home for this UI**

Search the frontend for where other org-admin-only Agent Studio or platform
settings already live (e.g. wherever `OrgModelGrant` management renders,
`frontend/components/app/provider-model-curation-dialog.tsx`'s containing page,
or an Agent Studio settings tab if one exists) — this is a SMALL admin
list+add-form, not a rich curation dialog like the model one; keep it
proportionate (a table of existing entries, a label+pattern input pair, an Add
button) rather than mirroring that dialog's full complexity.

- [ ] **Step 2: Write the failing test**

```typescript
it("lets an Org Admin add an allowlist entry and shows it in the list", async () => {
  const mockedList = vi.mocked(listImportSources);
  mockedList.mockResolvedValue({ sources: [] });
  const mockedCreate = vi.mocked(createImportSource);
  mockedCreate.mockResolvedValue({ id: "src-1", source_pattern: "https://github.com/acme-org/", label: "Acme" });

  const user = userEvent.setup();
  render(<ImportSourceAllowlist />, { wrapper: /* this file's established QueryClientProvider wrapper */ });

  await user.type(screen.getByLabelText(/label/i), "Acme");
  await user.type(screen.getByLabelText(/pattern|url/i), "https://github.com/acme-org/");
  await user.click(screen.getByRole("button", { name: /add/i }));

  await waitFor(() => expect(mockedCreate).toHaveBeenCalledWith({
    source_pattern: "https://github.com/acme-org/", label: "Acme",
  }));
});
```

Adjust field selectors to match your actual implementation; the intent (fill
label+pattern, submit, confirm the real API call fires with both fields) is
what matters.

- [ ] **Step 3: Implement**

A `useQuery` on `listImportSources`, a small form (label + pattern text inputs,
an Add button) whose `useMutation` calls `createImportSource` and invalidates
the list query on success. Render the existing entries as a simple list/table
(label, pattern, created_by). Gate the ADD form specifically on the viewer being
an Org Admin (check how this repo's frontend already knows "is the viewer an
org admin" — likely `effectivePlatformRole()`/a capability check already used
elsewhere for org-only UI — reuse it; the list itself renders for anyone who can
reach this page, matching the backend's read-is-open/write-is-org-admin split).

- [ ] **Step 4: Run the test, then typecheck and the full suite**

Run: `cd frontend && npm test -- --run import-source-allowlist && npm run typecheck && npm test -- --run`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/agent-studio/import-source-allowlist.tsx frontend/components/agent-studio/__tests__/import-source-allowlist.test.tsx
git commit -m "feat: Org Admin management UI for the import-source allowlist"
```

(add whatever settings-page file you modified in Step 1 to this commit too)

---

### Task 8: Docstrings — describe the finished import + screening scheme

**Files:**
- Modify: `backend/shared/routers/agent_skills.py` (module docstring)

**Interfaces:** none (docs-only; no code or test changes in this task).

- [ ] **Step 1: Update the module docstring**

Add a section (after the existing EVALUATION GATE section from sub-project 4)
describing: `POST /agent-skills/import` mirrors `create_skill`'s authorization
and write sequence exactly, with three screens inserted before lint — prompt-
injection (the same `FORBIDDEN_PATTERNS`-backed `lint_skill_fields` call every
write already makes), credential leakage (`scan_for_credentials`, reusing
`sandbox_policy.py`'s `_SECRET_PATTERNS` detection, never its redaction), and
provenance (`same_tenant_bu` via `administered_workspace_ids`, `external` via
the new Org-Admin-only `import_source_allowlist`, both refusing with the same
`SOURCE_NOT_ALLOWED` code). No new governance request type — a non-owner's
import proposes through the existing `agent_default_*` flow exactly like
`create_skill` already does. Note the explicit scope boundary: no live external
fetching (paste-and-declare only), no quarantine state (refuse-outright only),
Skills-only (no Behavior import).

- [ ] **Step 2: Verify no code changed**

Run: `git diff --stat` — confirm only `agent_skills.py` appears, with only
docstring lines changed.

- [ ] **Step 3: Commit**

```bash
git add backend/shared/routers/agent_skills.py
git commit -m "docs: describe the import + supply-chain screening scheme"
```
