# Orchestrator Phase 4 — Deliverables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Orchestrator persist what its agents produce, and render it agent-wise in the right-hand panel, as its own concept ("Deliverables") kept separate from the approval-gated `artifacts` the standalone agents write.

**Architecture:** A new `orchestrator_deliverables` table holds one row per document an agent produces, never overwritten, so re-running an agent adds a version instead of destroying the last. `orchestrator2/deliverables.py` splits into a pure renderer (turn text → rows, no IO) and a thin persistence shell, mirroring how `shared/services/orchestrator/artifacts_view.py` is already split. Capture runs inside `dispatch.run_agent` just before it yields `stream_end`. Pointers (the Development code tree, per-agent file trees, the PR link) are **synthesized at read time** from run state rather than stored, so they cannot stack duplicates across turns.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / Alembic / LangGraph; Next.js 15 / React 19 / TypeScript / Zod / Vitest.

**Spec:** `docs/superpowers/specs/2026-09-06-orchestrator-phase-4-deliverables-design.md`

## Global Constraints

- **The Orchestrator's agents are not the standalone agents.** Same names, same capability, different things. A Deliverable is NEVER written to the `artifacts` table and NEVER gains an `approval_status`. (Spec §1, user directive.)
- **Say "Business Unit" in prose, never "workspace".** Code identifiers still literally say `workspace`.
- **Project scope is load-bearing.** BYOK, models and connectors come from the run's project. `project_id` is passed from the verified `runs` row, never from a client frame, and never defaulted.
- **RBAC must not regress.** Project Admin only; a Project Admin of project A cannot reach project B's run. Every new read carries an explicit `tenant_id` predicate — RLS is inert in this deployment (the app connects as a `rolbypassrls` superuser).
- **The nine agent ids** are `requirements design plan development code_review security testing deployment documentation`. `plan` is the **Project Manager agent** in all user-facing text — never "Plan agent", never "PM agent".
- **Wire field is `agent`; DB column is `agent_id`.** The mapping happens once, in the persistence layer.
- Backend tests run as `cd backend && uv run python -m pytest ...` — bare `uv run pytest` fails with `ModuleNotFoundError: config`.
- `npm run lint` has 2 pre-existing errors unrelated to this branch; lint your own files with `npx eslint components/orchestrator lib/orchestrator`.
- **A green suite is not evidence.** Every task ends by breaking the implementation and confirming a test fails. A mutation reporting SURVIVED is not a result until you show `git diff --numstat` — `str.replace` no-ops silently on a non-matching pattern and the suite then prints green for an unmodified file. Restore mutations in a `finally`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/migrations/versions/0044_orchestrator_deliverables.py` | The table, its indexes, its RLS policies |
| `backend/shared/models/orm.py` (modify) | `OrchestratorDeliverable` ORM model |
| `backend/agents_orchestrator/orchestrator2/deliverables.py` (create) | Pure renderer + persistence + reads |
| `backend/agents_orchestrator/orchestrator2/dispatch.py` (modify) | Accumulate reply text; capture; emit `deliverable.ready` |
| `backend/agents_orchestrator/orchestrator2/context.py` (modify) | `_load_run_artifacts` reads the table, latest per agent |
| `backend/shared/routers/runs.py` (modify) | `GET /runs/{id}/deliverables` |
| `frontend/lib/orchestrator/deliverables.ts` (create) | Zod `Deliverable` + `DeliverableReadyEvent` |
| `frontend/lib/orchestrator/protocol.ts` (modify) | Fold the new event into the union |
| `frontend/lib/orchestrator/use-orchestrator-socket.ts` (modify) | Accumulate deliverables |
| `frontend/app/api/runs/[id]/deliverables/route.ts` (create) | BFF proxy |
| `frontend/components/orchestrator/cockpit.tsx` (modify) | Pass real `runId`, `activeStage`, deliverables |
| `frontend/components/orchestrator/artifacts-panel.tsx` (modify) | `tabLabel` prop, newest-first ordering, version timestamps |

---

### Task 1: The table and the ORM model

**Files:**
- Create: `backend/migrations/versions/0044_orchestrator_deliverables.py`
- Modify: `backend/shared/models/orm.py` (append after the `Artifact` class, which ends before `class Deployment`)
- Test: `backend/tests/orchestrator2/test_deliverables_schema.py`

**Interfaces:**
- Consumes: `AGENT_IDS` from `agents_orchestrator.orchestrator2.registry`
- Produces: `OrchestratorDeliverable` ORM model; table `orchestrator_deliverables`; module constant `DELIVERABLE_AGENT_IDS` on the migration module

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_deliverables_schema.py`:

```python
"""The deliverables table, and the one thing about it that can silently drift.

The `agent_id` CHECK constraint duplicates the registry. If a tenth agent is added
to `AGENT_IDS` and this constraint is not migrated with it, that agent's output is
rejected by the database at write time — which, because capture is deliberately
non-fatal, surfaces as an agent that produced nothing. Exactly the §2.4 failure
this rebuild exists to eliminate. This test is the drift guard.
"""
import re
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / "0044_orchestrator_deliverables.py"
)


def test_agent_id_check_constraint_matches_the_registry():
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    source = MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"agent_id IN \(([^)]*)\)", source)
    assert match, "the agent_id CHECK constraint is missing from the migration"
    in_constraint = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert in_constraint == set(AGENT_IDS), (
        f"constraint {sorted(in_constraint)} != registry {sorted(AGENT_IDS)}"
    )


def test_table_has_no_approval_column():
    """A Deliverable is not an Artifact. The absence of approval is the point:
    the Orchestrator is Project-Admin-only and has no gates (spec D5), so a
    column named `approval_status` here would be a contradiction someone would
    later 'fix' by wiring a gate into a surface that must not have one."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "approval_status" not in source


def test_rls_keys_off_current_tenant_id():
    """`app.tenant_id` matches nothing and reads as a permanently empty table,
    which looks exactly like 'no deliverables yet'. See 0043's docstring."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "app.current_tenant_id" in source
    assert "current_setting('app.tenant_id'" not in source
    assert "FORCE ROW LEVEL SECURITY" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_schema.py -v`
Expected: FAIL — the migration file does not exist (`FileNotFoundError`).

- [ ] **Step 3: Write the migration**

`backend/migrations/versions/0044_orchestrator_deliverables.py`:

```python
"""`orchestrator_deliverables` — what the Orchestrator's agents produce.

NOT `artifacts`. That table carries `approval_status` because a STANDALONE agent
wrote the row and a human has to accept it. The Orchestrator is Project-Admin-only
and has no gates (spec D5), so its output is a different thing with a different
name, and this table deliberately has no approval column at all.

NOTHING IS EVER OVERWRITTEN. Any agent can run at any time in this engine, and the
same agent can run repeatedly in one conversation. A re-run APPENDS a row; the panel
orders by `created_at DESC` and shows every version. An UPDATE here would silently
destroy a document the user asked for.

RLS KEYS OFF `app.current_tenant_id`, NOT `app.tenant_id`. Every other tenant-scoped
policy in this database uses that setting name and `get_db_session_for_tenant` sets
it transaction-locally. A policy written against the other name matches nothing and
the table reads as permanently empty - indistinguishable from "no deliverables yet".
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0044_orchestrator_deliverables"
down_revision = "0043_deployments"
branch_labels = None
depends_on = None

_TABLE = "orchestrator_deliverables"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable to match `runs.project_id`, which is nullable since 0005 for
        # webhook-triggered runs. A NOT NULL here would refuse to record a
        # deliverable for a run the platform itself allows to exist.
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),

        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("language", sa.String(50)),
        sa.Column("source", sa.String(50)),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),

        # The nine. Kept in sync with `registry.AGENT_IDS` by
        # tests/orchestrator2/test_deliverables_schema.py.
        sa.CheckConstraint(
            "agent_id IN ('requirements','design','plan','development',"
            "'code_review','security','testing','deployment','documentation')",
            name="ck_orchestrator_deliverables_agent_id",
        ),
        sa.CheckConstraint(
            "kind IN ('markdown','mermaid','openapi','code','image','download',"
            "'code-tree','file-tree','link')",
            name="ck_orchestrator_deliverables_kind",
        ),
    )

    # The panel read, exactly: this run, newest first.
    op.create_index(
        "ix_orchestrator_deliverables_run_created", _TABLE,
        ["run_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_orchestrator_deliverables_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_orchestrator_deliverables_project_id", _TABLE, ["project_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {_TABLE} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    # FORCE, so the table owner is subject to the policy too.
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_index("ix_orchestrator_deliverables_project_id", table_name=_TABLE)
    op.drop_index("ix_orchestrator_deliverables_tenant_id", table_name=_TABLE)
    op.drop_index("ix_orchestrator_deliverables_run_created", table_name=_TABLE)
    op.drop_table(_TABLE)
```

- [ ] **Step 4: Add the ORM model**

In `backend/shared/models/orm.py`, immediately after the `Artifact` class (before `class Deployment`):

```python
class OrchestratorDeliverable(Base):
    """One document an Orchestrator agent produced. Append-only.

    NOT an `Artifact`. `artifacts` rows carry `approval_status` because a standalone
    agent wrote them and a human accepts them; the Orchestrator is Project-Admin-only
    and has no gates, so its output is a separate concept with no approval column.

    Append-only by design: any agent can run at any time here, and the same agent can
    run repeatedly in one conversation. Every run is kept and the panel shows them
    newest-first, so a re-run never destroys the document it replaces.
    """
    __tablename__ = "orchestrator_deliverables"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Nullable to match `runs.project_id` (nullable since 0005 for webhook runs).
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)

    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_schema.py -v`
Expected: 3 passed.

- [ ] **Step 6: Apply the migration and confirm the table exists**

```bash
cd backend && uv run alembic upgrade head
```
Then confirm, against the compose Postgres on **5433** (not 5432 — `docs/local-setup.md` is wrong about this):
```bash
docker exec sdlc-postgres psql -U postgres -d sdlc_product -c "\d orchestrator_deliverables"
```
Expected: the table, both indexes, both policies, and NO `approval_status` column.

- [ ] **Step 7: Prove the drift guard actually fails**

Temporarily delete `'plan'` from the migration's `agent_id` CHECK, re-run the test, confirm `test_agent_id_check_constraint_matches_the_registry` FAILS, then restore it and confirm green. Show `git diff --numstat` for both states — a SURVIVED mutation you cannot see the diff for is not a result.

- [ ] **Step 8: Commit**

```bash
git add backend/migrations/versions/0044_orchestrator_deliverables.py backend/shared/models/orm.py backend/tests/orchestrator2/test_deliverables_schema.py
git commit -m "feat(orchestrator2): the orchestrator_deliverables table, append-only and ungated

Separate from `artifacts` because that table's approval_status exists BECAUSE a
standalone agent wrote the row. The Orchestrator has no gates (D5), so its output
is a different concept and this table has no approval column at all.

Append-only: any agent can run at any time and the same agent can run repeatedly,
so a re-run adds a version rather than destroying the last."
```

---

### Task 2: The pure renderer

**Files:**
- Create: `backend/agents_orchestrator/orchestrator2/deliverables.py`
- Test: `backend/tests/orchestrator2/test_deliverables_render.py`

**Interfaces:**
- Consumes: `parse_design_markdown` from `shared.services.orchestrator.artifacts_view`; `AGENT_IDS` from `registry`
- Produces:
  - `MIN_DELIVERABLE_CHARS: int = 200`
  - `render(agent_id: str, reply_text: str) -> list[dict]` — rows with keys `agent`, `kind`, `title`, `content`, and optionally `language`. No `id` (persistence assigns the row uuid). Pure: no IO.
  - `derive_title(agent_id: str, body: str) -> str`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_deliverables_render.py`:

```python
"""Turning an agent's turn into deliverable rows. Pure — no database here.

The threshold exists because an agent's clarifying question is not a document.
"Which service did you mean?" must stay in chat; a PRD must not.
"""
import pytest

from agents_orchestrator.orchestrator2.deliverables import (
    MIN_DELIVERABLE_CHARS,
    derive_title,
    render,
)

_LONG = "x" * (MIN_DELIVERABLE_CHARS + 10)


def test_a_short_reply_is_not_a_deliverable():
    assert render("security", "Which service did you mean?") == []


def test_a_substantive_reply_becomes_one_markdown_row():
    rows = render("security", _LONG)
    assert len(rows) == 1
    assert rows[0]["agent"] == "security"
    assert rows[0]["kind"] == "markdown"
    assert rows[0]["content"] == _LONG


def test_the_project_manager_agent_produces_a_deliverable_like_any_other():
    """`plan` is the Project Manager agent. `sections_from_run` never had a branch
    for it, so `plan_artifacts` was written by nothing and read by nothing and its
    output rendered nowhere. Nothing about this agent is special; that was the bug."""
    rows = render("plan", _LONG)
    assert len(rows) == 1 and rows[0]["agent"] == "plan"


def test_every_registry_agent_can_produce_a_deliverable():
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS
    for agent_id in AGENT_IDS:
        assert render(agent_id, _LONG), f"{agent_id} produced nothing"


def test_design_is_split_into_its_sections():
    md = (
        "## High-Level Design\n" + "a" * 120 + "\n\n"
        "## Database Schema\n" + "b" * 120 + "\n"
    )
    rows = render("design", md)
    titles = [r["title"] for r in rows]
    assert "High-Level Design (HLD)" in titles
    assert "Database Schema" in titles
    assert all(r["agent"] == "design" for r in rows)


def test_design_that_does_not_parse_still_yields_one_row():
    """Falling through to nothing would lose the document entirely — the failure
    mode is invisible, because an empty panel looks like an agent that said little."""
    rows = render("design", _LONG)
    assert len(rows) == 1 and rows[0]["kind"] == "markdown"


def test_title_comes_from_the_first_heading_when_there_is_one():
    assert derive_title("requirements", "# Billing Rework PRD\nbody") == "Billing Rework PRD"


def test_title_falls_back_to_the_agent_name():
    assert derive_title("code_review", "no heading here") == "Code Review Report"


def test_the_plan_agent_is_named_project_manager_in_titles():
    """User-facing text says Project Manager agent, never 'Plan agent'."""
    assert derive_title("plan", "no heading here") == "Project Manager Report"


def test_render_is_pure_and_touches_no_database():
    import inspect
    from agents_orchestrator.orchestrator2 import deliverables
    src = inspect.getsource(deliverables.render)
    for forbidden in ("session", "select(", "await ", "commit"):
        assert forbidden not in src, f"render() must stay pure; found {forbidden!r}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_render.py -v`
Expected: FAIL — `ModuleNotFoundError: agents_orchestrator.orchestrator2.deliverables`.

- [ ] **Step 3: Write the implementation**

`backend/agents_orchestrator/orchestrator2/deliverables.py` (renderer half only; persistence lands in Task 3):

```python
"""What an Orchestrator agent produced, and how it is stored and read back.

A DELIVERABLE IS NOT AN ARTIFACT. The `artifacts` table carries `approval_status`
because a STANDALONE agent wrote the row and a human accepts it. The Orchestrator's
agents share the standalone agents' names and capability and are not the same thing:
the runner is a Project Admin who already owns all nine, and there are no gates
(spec D5). So Orchestrator output is a separate concept in a separate table with no
approval column, and nothing here ever writes to `artifacts`.

This module is split the way `shared/services/orchestrator/artifacts_view.py` is —
a PURE core (`render`, `derive_title`) with no IO, and a thin persistence shell
around it — so the part that decides what a turn produced is testable without a
database.

POINTERS ARE NOT STORED. The Development code tree, a per-agent file tree and the
pull-request link are not documents; they are references to state that already lives
elsewhere (the run workspace, `runs.development_artifacts`). Storing one per turn
would stack a duplicate row on every turn the agent ran. They are synthesized on
read instead, with stable ids — see `pointers_for_run`.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agents_orchestrator.orchestrator2.registry import AGENT_IDS
from shared.services.orchestrator.artifacts_view import parse_design_markdown

logger = logging.getLogger(__name__)

#: Below this, a reply is conversation rather than a document. An agent asking
#: "which service did you mean?" must not create a deliverable; a PRD must. The
#: value is the Copilot's, kept so behaviour does not shift under the rename.
MIN_DELIVERABLE_CHARS = 200

#: User-facing agent names. `plan` is the PROJECT MANAGER agent and is never
#: called "Plan agent" or "PM agent" anywhere a user can read it.
DISPLAY_NAME: dict[str, str] = {
    "requirements": "Requirements",
    "design": "Design",
    "plan": "Project Manager",
    "development": "Development",
    "code_review": "Code Review",
    "security": "Security",
    "testing": "Testing",
    "deployment": "Deployment",
    "documentation": "Documentation",
}

# Every agent must have a display name, or a deliverable renders under a blank
# heading. Checked at import rather than at the first turn that hits the gap.
_missing = set(AGENT_IDS) - set(DISPLAY_NAME)
if _missing:  # pragma: no cover - import-time guard
    raise RuntimeError(f"DISPLAY_NAME is missing agents: {sorted(_missing)}")

_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$")


def derive_title(agent_id: str, body: str) -> str:
    """A distinguishing title for one deliverable.

    The document's own first heading when it has one — several versions of the same
    agent's output sit under one heading in the panel, so "Requirements Report" three
    times over tells the reader nothing about which is which.
    """
    match = _HEADING_RE.search(body or "")
    if match and match.group(1).strip():
        return match.group(1).strip()
    return f"{DISPLAY_NAME.get(agent_id, agent_id)} Report"


def render(agent_id: str, reply_text: str) -> list[dict]:
    """Turn one agent turn into deliverable rows. PURE — no IO, no database.

    Returns `[]` when the turn produced conversation rather than a document. Rows
    carry no `id`: the persistence layer assigns the row uuid, so two versions of
    the same document can never collide on a slug.
    """
    body = (reply_text or "").strip()
    if len(body) < MIN_DELIVERABLE_CHARS:
        return []

    if agent_id == "design":
        sections, _persist = parse_design_markdown(body)
        if sections:
            # `parse_design_markdown` speaks the panel's `stage` vocabulary; this
            # module speaks `agent`. Translate once, here, rather than teaching the
            # rest of the pipeline to accept both.
            return [
                {
                    "agent": agent_id,
                    "kind": section.get("kind", "markdown"),
                    "title": section.get("title", derive_title(agent_id, body)),
                    "content": section.get("content", ""),
                }
                for section in sections
            ]
        # Fall through deliberately: a design turn that does not parse is still a
        # document, and dropping it would lose the whole reply behind an empty panel.

    return [{
        "agent": agent_id,
        "kind": "markdown",
        "title": derive_title(agent_id, body),
        "content": body,
    }]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_render.py -v`
Expected: 10 passed.

- [ ] **Step 5: Prove the threshold test can fail**

Change `MIN_DELIVERABLE_CHARS` to `0`, re-run, confirm `test_a_short_reply_is_not_a_deliverable` FAILS. Restore in a `finally`; show `git diff --numstat` for both states.

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/deliverables.py backend/tests/orchestrator2/test_deliverables_render.py
git commit -m "feat(orchestrator2): pure renderer turning an agent turn into deliverables

Design reuses the existing pure parse_design_markdown rather than a second
implementation, and falls through to one markdown row when a design turn does not
parse - dropping it would lose the reply behind an empty panel, which reads as an
agent that said nothing.

`plan` is the Project Manager agent and gets no special case; that it had one
(none at all, in sections_from_run) is why its output rendered nowhere."
```

---

### Task 3: Persistence and reads

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/deliverables.py`
- Test: `backend/tests/orchestrator2/test_deliverables_store.py`

**Interfaces:**
- Consumes: `render` (Task 2); `OrchestratorDeliverable` (Task 1); `get_db_session_for_tenant` from `shared.db`
- Produces:
  - `async capture(agent_id: str, reply_text: str, *, run_id: str, tenant_id: str, project_id: str | None) -> list[dict]`
  - `async deliverables_for_run(run_id: str, tenant_id: str) -> list[dict]` — newest first
  - `async latest_per_agent(run_id: str, tenant_id: str) -> dict[str, Any]`
  - `pointers_for_run(dev_artifacts: dict | None) -> list[dict]` — pure
  - `class DeliverableWriteError(Exception)`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_deliverables_store.py`:

```python
"""Persisting and reading deliverables.

The tenant tests patch the SESSION FACTORY, never the function under test, and the
fake session EVALUATES the query's where-clause against a store holding the same run
id under two different tenants. Asserting on a mock of the function under test would
prove nothing; this fake hands back the wrong tenant's rows the moment the predicate
is dropped. Same shape as tests/orchestrator2/test_context.py, deliberately.
"""
import uuid
from contextlib import asynccontextmanager

import pytest

_RUN = "11111111-1111-1111-1111-111111111111"
_TENANT = "22222222-2222-2222-2222-222222222222"
_OTHER_TENANT = "33333333-3333-3333-3333-333333333333"


class _Row:
    def __init__(self, tenant_id, agent_id, title, created_at, content="body"):
        self.id = uuid.uuid4()
        self.run_id = uuid.UUID(_RUN)
        self.tenant_id = uuid.UUID(tenant_id)
        self.project_id = None
        self.agent_id = agent_id
        self.kind = "markdown"
        self.title = title
        self.content = content
        self.url = None
        self.language = None
        self.source = None
        self.created_at = created_at


def _fake_factory(monkeypatch, rows, added=None):
    """Patch the session factory with one that filters `rows` by the query's
    tenant predicate, so dropping the predicate changes the RESULT, not a mock call."""
    import shared.db as shared_db

    class _Result:
        def __init__(self, items): self._items = items
        def scalars(self): return self
        def all(self): return list(self._items)

    class _Session:
        async def execute(self, stmt):
            text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            keep = [r for r in rows if str(r.tenant_id) in text and str(r.run_id) in text]
            keep.sort(key=lambda r: r.created_at, reverse=True)
            return _Result(keep)
        def add(self, obj):
            if added is not None:
                added.append(obj)
        async def commit(self): pass

    @asynccontextmanager
    async def _fake(tenant_id):
        yield _Session()

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", _fake)


@pytest.mark.asyncio
async def test_read_is_newest_first(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    rows = [
        _Row(_TENANT, "security", "old", 1),
        _Row(_TENANT, "security", "new", 2),
    ]
    _fake_factory(monkeypatch, rows)
    out = await deliverables.deliverables_for_run(_RUN, _TENANT)
    assert [d["title"] for d in out] == ["new", "old"]


@pytest.mark.asyncio
async def test_read_refuses_another_tenants_rows(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    rows = [_Row(_OTHER_TENANT, "security", "theirs", 1)]
    _fake_factory(monkeypatch, rows)
    assert await deliverables.deliverables_for_run(_RUN, _TENANT) == []


@pytest.mark.asyncio
async def test_latest_per_agent_keeps_only_the_newest_of_each(monkeypatch):
    """Every version is VISIBLE, but only the newest FEEDS an agent - otherwise a
    downstream agent is handed two contradictory PRDs and has to guess."""
    from agents_orchestrator.orchestrator2 import deliverables
    rows = [
        _Row(_TENANT, "requirements", "PRD v1", 1, content="one"),
        _Row(_TENANT, "requirements", "PRD v2", 2, content="two"),
        _Row(_TENANT, "design", "HLD", 3, content="hld"),
    ]
    _fake_factory(monkeypatch, rows)
    latest = await deliverables.latest_per_agent(_RUN, _TENANT)
    assert set(latest) == {"requirements", "design"}
    assert latest["requirements"]["title"] == "PRD v2"


@pytest.mark.asyncio
async def test_capture_appends_and_never_updates(monkeypatch):
    """A re-run must not destroy the document it replaces."""
    from agents_orchestrator.orchestrator2 import deliverables
    added = []
    _fake_factory(monkeypatch, [], added=added)
    body = "x" * 400
    await deliverables.capture("security", body, run_id=_RUN, tenant_id=_TENANT, project_id=None)
    await deliverables.capture("security", body, run_id=_RUN, tenant_id=_TENANT, project_id=None)
    assert len(added) == 2, "the second capture replaced the first instead of appending"


@pytest.mark.asyncio
async def test_capture_of_a_short_reply_writes_nothing(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    added = []
    _fake_factory(monkeypatch, [], added=added)
    out = await deliverables.capture("security", "ok", run_id=_RUN, tenant_id=_TENANT, project_id=None)
    assert out == [] and added == []


def test_pointers_are_not_versioned_and_carry_stable_ids():
    """The panel de-dupes the Development tree on the literal id `dev-code`; a
    per-turn uuid there would stack a new tree on every turn."""
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    out = pointers_for_run({"repo_url": "https://dev.azure.com/x/_git/y",
                            "pr_url": "https://dev.azure.com/x/_git/y/pullrequest/3"})
    by_id = {p["id"]: p for p in out}
    assert by_id["dev-code"]["kind"] == "code-tree"
    assert by_id["dev-code"]["agent"] == "development"
    assert by_id["dev-pr"]["kind"] == "link"
    assert by_id["dev-pr"]["url"].endswith("/pullrequest/3")


def test_no_pointers_without_a_pulled_repo():
    """An empty code tree implies the agent pulled something. It must not."""
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    assert pointers_for_run(None) == []
    assert pointers_for_run({}) == []


def test_an_agent_that_generated_files_gets_a_file_tree():
    """Each agent's own frontend quirks carry over — a stage that wrote files to disk
    surfaces them under its own heading, the way Development surfaces its clone."""
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    out = pointers_for_run(None, {"testing", "documentation"})
    by_id = {p["id"]: p for p in out}
    assert by_id["testing-files"]["kind"] == "file-tree"
    assert by_id["testing-files"]["source"] == "testing"
    assert by_id["documentation-files"]["agent"] == "documentation"


def test_development_does_not_get_a_second_tree_over_the_same_clone():
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    out = pointers_for_run({"repo_url": "https://x"}, {"development"})
    assert [p["id"] for p in out] == ["dev-code"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_store.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'deliverables_for_run'`.

- [ ] **Step 3: Append the implementation to `deliverables.py`**

```python
class DeliverableWriteError(Exception):
    """A deliverable could not be persisted.

    Raised rather than swallowed. The CALLER decides that a failed capture must not
    fail the turn (the agent has done its work and the user has read the reply), but
    that decision is made once, visibly, at the call site - not hidden here behind a
    bare `except`. A silent swallow is how the Project Manager agent stayed
    undispatchable without anyone noticing.
    """


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _to_wire(row: Any) -> dict:
    """One ORM row as the panel's shape. `agent_id` on the column, `agent` on the
    wire - the mapping happens here and nowhere else."""
    return {
        "id": str(row.id),
        "agent": row.agent_id,
        "kind": row.kind,
        "title": row.title,
        "content": row.content or "",
        "url": row.url,
        "language": row.language,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def pointers_for_run(
    dev_artifacts: dict | None,
    stages_with_files: set[str] | None = None,
) -> list[dict]:
    """Reference rows synthesized on read. PURE — no IO.

    These are not documents and are not stored: the code tree lives in the run
    workspace, the generated files live on disk, and the PR lives in Azure DevOps.
    Storing one per turn would stack a duplicate on every turn. Their ids are STABLE
    (`dev-code`, `dev-pr`, `<agent>-files`) because the panel de-dupes the Development
    tree on the literal id `dev-code`.

    `stages_with_files` is computed by the CALLER, which is the only place that can
    look at the disk. Passing it in keeps this function pure and testable, and means
    a caller that cannot check the disk simply shows no file trees rather than
    guessing that some exist.
    """
    out: list[dict] = []
    if isinstance(dev_artifacts, dict) and dev_artifacts:
        if dev_artifacts.get("repo_url"):
            out.append({
                "id": "dev-code", "agent": "development", "kind": "code-tree",
                "title": "Repository code", "content": "", "url": None,
                "language": None, "source": "development", "created_at": None,
            })
        pr_url = dev_artifacts.get("pr_url") or dev_artifacts.get("pull_request_url")
        if pr_url:
            out.append({
                "id": "dev-pr", "agent": "development", "kind": "link",
                "title": "Pull request", "content": "", "url": pr_url,
                "language": None, "source": None, "created_at": None,
            })
    for agent_id in sorted(stages_with_files or ()):
        # `development` already has its code tree above; a second tree over the same
        # clone would be the same files twice under one heading.
        if agent_id == "development":
            continue
        out.append({
            "id": f"{agent_id}-files", "agent": agent_id, "kind": "file-tree",
            "title": "Generated files", "content": "", "url": None,
            "language": None, "source": agent_id, "created_at": None,
        })
    return out


async def capture(
    agent_id: str,
    reply_text: str,
    *,
    run_id: str,
    tenant_id: str,
    project_id: str | None,
) -> list[dict]:
    """Persist what this turn produced and return it in the panel's shape.

    `project_id` comes from the VERIFIED `runs` row, never from a client frame, and
    is keyword-required with no default for the same reason `run_agent`'s is: a
    default would let a future call site drop project scoping silently.
    """
    rows = render(agent_id, reply_text)
    if not rows:
        return []

    run_uuid, tenant_uuid = _as_uuid(run_id), _as_uuid(tenant_id)
    if run_uuid is None or tenant_uuid is None:
        raise DeliverableWriteError(f"run_id/tenant_id is not a uuid: {run_id!r}/{tenant_id!r}")

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import OrchestratorDeliverable

    written: list[dict] = []
    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            for row in rows:
                record = OrchestratorDeliverable(
                    run_id=run_uuid,
                    tenant_id=tenant_uuid,
                    project_id=_as_uuid(project_id) if project_id else None,
                    agent_id=row["agent"],
                    kind=row["kind"],
                    title=row["title"],
                    content=row.get("content") or "",
                    language=row.get("language"),
                )
                session.add(record)
                written.append(record)
            await session.commit()
            return [_to_wire(r) for r in written]
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error, never dropped
        raise DeliverableWriteError(str(exc)) from exc


async def _load(run_id: str, tenant_id: str) -> list[dict]:
    run_uuid, tenant_uuid = _as_uuid(run_id), _as_uuid(tenant_id)
    if run_uuid is None or tenant_uuid is None:
        return []

    from sqlalchemy import select

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import OrchestratorDeliverable

    async with get_db_session_for_tenant(tenant_id) as session:
        # BOTH predicates on purpose. `get_db_session_for_tenant` sets the tenant GUC
        # so RLS applies - but this deployment connects as a rolbypassrls superuser,
        # for whom policies do not apply at all, so the explicit tenant_id below is
        # what actually isolates tenants today. Neither is left as the only one.
        stmt = (
            select(OrchestratorDeliverable)
            .where(
                OrchestratorDeliverable.run_id == run_uuid,
                OrchestratorDeliverable.tenant_id == tenant_uuid,
            )
            .order_by(OrchestratorDeliverable.created_at.desc())
        )
        result = await session.execute(stmt)
        return [_to_wire(row) for row in result.scalars().all()]


async def deliverables_for_run(run_id: str, tenant_id: str) -> list[dict]:
    """Every version this run holds, newest first. Tenant-scoped."""
    return await _load(run_id, tenant_id)


async def latest_per_agent(run_id: str, tenant_id: str) -> dict[str, Any]:
    """The newest deliverable per agent, for an agent's hand-off context.

    Every version stays VISIBLE in the panel; only the newest is FED to an agent.
    Handing a downstream agent two versions of the same PRD makes it guess which one
    is current, and it will sometimes guess wrong.
    """
    latest: dict[str, Any] = {}
    for row in await _load(run_id, tenant_id):  # already newest-first
        latest.setdefault(row["agent"], row)
    return latest
```

Add `import uuid` to the module's imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_store.py -v`
Expected: 9 passed.

- [ ] **Step 5: Prove the tenant test can fail**

Delete the `OrchestratorDeliverable.tenant_id == tenant_uuid` line from `_load`, re-run, confirm `test_read_refuses_another_tenants_rows` FAILS. Restore in a `finally`; show `git diff --numstat` for both states. **If it still passes, the fake is not evaluating the predicate and the test is worthless — fix the fake before continuing.**

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/deliverables.py backend/tests/orchestrator2/test_deliverables_store.py
git commit -m "feat(orchestrator2): persist deliverables append-only, read newest-first

Every version is kept and visible; only the newest per agent feeds an agent's
context, so nothing downstream is handed two contradictory PRDs.

Pointers (the Development code tree, the PR link) are synthesized on read with
stable ids rather than stored - the panel de-dupes the tree on the literal id
`dev-code`, and a per-turn row there would stack a new tree every turn.

Reads carry an explicit tenant predicate AS WELL AS the tenant GUC: this
deployment connects as a rolbypassrls superuser, so RLS is not load-bearing."
```

---

### Task 4: Capture inside the turn

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/dispatch.py`
- Test: `backend/tests/orchestrator2/test_dispatch_deliverables.py`

**Interfaces:**
- Consumes: `capture` (Task 3)
- Produces: a `deliverable.ready` event — `{"type": "deliverable.ready", "run_id": str, "agent": str, "deliverables": list[dict]}` — yielded immediately before `stream_end`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_dispatch_deliverables.py`:

```python
"""Capture happens inside the turn, before it is declared finished.

Ordering is the point. `stream_end` is yielded from inside `run_agent`, so capturing
in `ws.py` after the loop would land `deliverable.ready` AFTER the client had already
been told the turn was over.

The fakes here are the ones `test_dispatch_streaming.py` already uses — same stubbed
model resolution, same scripted graph — so a change to the dispatch loop breaks both
files together rather than leaving this one passing against a shape that no longer
exists.
"""
import pytest
from langchain_core.messages import AIMessageChunk

from agents_orchestrator.orchestrator2 import dispatch, registry as reg
from shared.services import model_resolver as mr


@pytest.fixture(autouse=True)
def _stub_model_resolution(monkeypatch):
    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        return mr.ResolvedModel(
            provider="anthropic", litellm_provider="anthropic", model="m",
            api_key="k", base_url=None, alias="tenant:t1:p1",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


class _ScriptedGraph:
    def __init__(self, messages):
        self._messages = messages

    async def astream(self, state, stream_mode=None, config=None):
        for message in self._messages:
            yield (message, {})


async def _turn(monkeypatch, reply: str, *, project_id: str = "p1") -> list[dict]:
    """Run one `security` turn whose agent says exactly `reply`."""
    monkeypatch.setitem(
        reg.REGISTRY, "security",
        reg.AgentCapability(
            agent_id="security",
            load_graph=lambda: _ScriptedGraph([AIMessageChunk(content=reply)]),
            load_prompt=lambda: "SYS",
            mode="stream",
        ),
    )
    return [e async for e in dispatch.run_agent(
        "security", text="hi", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id=project_id,
        context="", reason="")]


def _events_of(events, kind):
    return [e for e in events if e.get("type") == kind]


@pytest.mark.asyncio
async def test_deliverable_ready_precedes_stream_end(monkeypatch):
    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        return [{"id": "d1", "agent": agent_id, "kind": "markdown",
                 "title": "Security Report", "content": reply_text}]

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    events = await _turn(monkeypatch, "x" * 400)
    types = [e["type"] for e in events]
    assert "deliverable.ready" in types
    assert types.index("deliverable.ready") < types.index("stream_end")


@pytest.mark.asyncio
async def test_capture_receives_exactly_what_the_user_saw(monkeypatch):
    """Accumulated from the EVENTS, not from the graph - so what is stored is what
    was displayed, even if the graph's internal state says otherwise."""
    seen = {}

    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        seen["text"] = reply_text
        seen["project_id"] = project_id
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    await _turn(monkeypatch, "hello world " * 40, project_id="p-1")
    assert seen["text"] == "hello world " * 40
    assert seen["project_id"] == "p-1", "project scope must reach capture"


@pytest.mark.asyncio
async def test_a_failed_capture_does_not_fail_the_turn(monkeypatch):
    """The agent did the work and the user read it. Losing the persistence step is a
    smaller harm than losing the reply - but it is SURFACED, never swallowed."""
    async def _boom(agent_id, reply_text, *, run_id, tenant_id, project_id):
        raise dispatch.deliverables.DeliverableWriteError("disk on fire")

    monkeypatch.setattr(dispatch.deliverables, "capture", _boom)
    events = await _turn(monkeypatch, "x" * 400)
    types = [e["type"] for e in events]
    assert types[-1] == "stream_end", "the turn must still end cleanly"
    assert _events_of(events, "error"), "a failed capture must be visible, not silent"


@pytest.mark.asyncio
async def test_nothing_is_emitted_when_the_turn_produced_no_deliverable(monkeypatch):
    """An empty `deliverable.ready` would make the panel flash a heading with nothing
    under it, which reads as a document that failed to load."""
    async def _capture(agent_id, reply_text, *, run_id, tenant_id, project_id):
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture", _capture)
    events = await _turn(monkeypatch, "ok")
    assert not _events_of(events, "deliverable.ready")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_dispatch_deliverables.py -v`
Expected: FAIL — `AttributeError: module 'dispatch' has no attribute 'deliverables'`.

- [ ] **Step 3: Wire capture into `run_agent`**

In `dispatch.py`, add the import near the other `orchestrator2` imports:

```python
from agents_orchestrator.orchestrator2 import deliverables
```

Inside `run_agent`, before the `try`, add the accumulator:

```python
    # What the USER saw, accumulated from the events this generator yields rather
    # than from the graph's state - so a deliverable is exactly the reply that was
    # displayed, never a different rendering of it.
    reply_parts: list[str] = []
```

At each `yield {"type": "stream_chunk", "content": ...}` site inside `run_agent`
(there are three — around lines 452, 466 and the tool-result branch), append the same
text to `reply_parts` immediately before the yield. Then mark success at the end of the
`try` body:

```python
        captured_ok = True
```
declaring `captured_ok = False` alongside `reply_parts`.

Finally, between the `finally:` block's end and the existing `yield {"type": "stream_end"}`, insert:

```python
    # AFTER the `finally` and BEFORE `stream_end`, deliberately.
    #
    # Not inside `finally`: nothing may yield there — on close, `GeneratorExit`
    # unwinds through it and a yield would become "async generator ignored
    # GeneratorExit". Not in `ws.py` after the loop either: `stream_end` is yielded
    # from HERE, so capturing outside would land `deliverable.ready` after the client
    # had already been told the turn was finished.
    #
    # Only on a turn that actually completed. Capturing a partial reply from a failed
    # turn would file a truncated document under the agent's heading, where it is
    # indistinguishable from a complete one.
    if captured_ok:
        try:
            produced = await deliverables.capture(
                agent_id,
                "".join(reply_parts),
                run_id=run_id,
                tenant_id=tenant_id,
                # From the verified `runs` row, like every other project-scoped
                # value on this path. Never from the client frame.
                project_id=project_id,
            )
            if produced:
                yield {"type": "deliverable.ready", "run_id": run_id,
                       "agent": agent_id, "deliverables": produced}
        except Exception as exc:  # noqa: BLE001 - a failed capture must not fail the turn
            # The agent did the work and the user has read the reply; losing the
            # persistence step is the smaller harm. But it is SURFACED and logged,
            # never swallowed - a silent `except` here is precisely how the old
            # engine's missing agents went unnoticed for so long.
            logger.exception(
                "orchestrator2 could not persist a deliverable (agent=%s run=%s)",
                agent_id, run_id,
            )
            yield {"type": "error", "agent": agent_id,
                   "message": "The reply could not be saved to Deliverables.",
                   "detail": str(exc)}

    yield {"type": "stream_end"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/ -q`
Expected: all pass (363 existing + the new ones).

- [ ] **Step 5: Prove the ordering test can fail**

Move the capture block to AFTER `yield {"type": "stream_end"}`, re-run, confirm `test_deliverable_ready_precedes_stream_end` FAILS. Restore in a `finally`; show `git diff --numstat` for both states.

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/dispatch.py backend/tests/orchestrator2/test_dispatch_deliverables.py backend/tests/orchestrator2/conftest.py
git commit -m "feat(orchestrator2): capture deliverables inside the turn, before stream_end

Ordering is deliberate: stream_end is yielded from inside run_agent, so capturing
in ws.py after the loop would land deliverable.ready after the client had already
been told the turn was over. It cannot go in the `finally` either - nothing may
yield there without turning a close into 'async generator ignored GeneratorExit'.

Reply text is accumulated from the EVENTS, so what is stored is what was displayed.
A failed capture surfaces a typed error and still ends the turn cleanly; it is
logged at exception level and never swallowed."
```

---

### Task 5: Context reads deliverables

**Files:**
- Modify: `backend/agents_orchestrator/orchestrator2/context.py`
- Modify: `backend/tests/orchestrator2/test_context.py`

**Interfaces:**
- Consumes: `latest_per_agent` (Task 3)
- Produces: `_load_run_artifacts(run_id, tenant_id) -> dict[str, Any]` — same signature, new source. `handoff_context`'s signature is unchanged.

**Ruling to record in the phase ledger:** `context.py` reads the deliverables table
**only**, not the run's legacy `*_artifacts` columns. A run driven by the old Copilot
therefore shows the Orchestrator no context. That is correct rather than a regression:
those columns are what the STANDALONE agents write, and the user's directive is that the
two must not be confused. Phase 5 deletes the Copilot. Cost if wrong: a run started in
the Copilot and continued in the Orchestrator loses its hand-off — recoverable by
re-running the agent.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/orchestrator2/test_context.py`:

```python
@pytest.mark.asyncio
async def test_context_reads_deliverables_not_run_columns(monkeypatch):
    """The run's `*_artifacts` columns are what the STANDALONE agents write. The
    Orchestrator's agents are a different thing and write deliverables; reading the
    columns here would mix the two concepts the user asked be kept apart."""
    from agents_orchestrator.orchestrator2 import context, deliverables

    async def _latest(run_id, tenant_id):
        return {"requirements": {"title": "PRD v2", "content": "the newest PRD"}}

    monkeypatch.setattr(deliverables, "latest_per_agent", _latest)
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "the newest PRD" in out


@pytest.mark.asyncio
async def test_only_the_newest_version_reaches_an_agent(monkeypatch):
    """Every version stays visible in the panel. Feeding two versions of one PRD to a
    downstream agent makes it guess which is current, and it will sometimes guess
    wrong."""
    from agents_orchestrator.orchestrator2 import context, deliverables

    async def _latest(run_id, tenant_id):
        return {"requirements": {"title": "PRD v2", "content": "NEWEST"}}

    monkeypatch.setattr(deliverables, "latest_per_agent", _latest)
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "NEWEST" in out and "PRD v1" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_context.py -v`
Expected: the two new tests FAIL (context still reads run columns).

- [ ] **Step 3: Repoint `_load_run_artifacts`**

Replace the body of `_load_run_artifacts` in `context.py` so it delegates:

```python
async def _load_run_artifacts(run_id: str, tenant_id: str) -> dict[str, Any]:
    """What this run holds, keyed by the agent that produced it, newest version only.

    READS DELIVERABLES, NOT THE RUN'S `*_artifacts` COLUMNS. Those columns are what
    the STANDALONE agents write; the Orchestrator's agents share their names and
    capability and are a different thing, and mixing the two is exactly what the
    separate table exists to prevent.

    Raises `ContextUnavailableError` on a failed read. Returning `{}` would reach the
    agent as "this run holds nothing", so it would re-ask the user for work already
    done - the defect the old `_upstream_context` had.
    """
    from agents_orchestrator.orchestrator2 import deliverables
    try:
        latest = await deliverables.latest_per_agent(run_id, tenant_id)
    except Exception as exc:  # noqa: BLE001
        raise ContextUnavailableError(str(exc)) from exc
    return {
        agent_id: {"title": row.get("title"), "content": row.get("content") or ""}
        for agent_id, row in latest.items()
    }
```

Delete `ARTIFACT_COLUMNS`, `_build_artifact_columns` and `ArtifactColumnError` — now dead. Update the module docstring: the import-time proof that every agent resolves a real `runs` column is replaced by the `agent_id` CHECK-vs-`AGENT_IDS` guard in `tests/orchestrator2/test_deliverables_schema.py`. Say so explicitly, and remove any sentence that still claims the column-based guarantee.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/ -q`
Expected: all pass. Any test asserting on `ARTIFACT_COLUMNS` must be updated, not deleted — replace its assertion with the equivalent against the CHECK guard.

- [ ] **Step 5: Prove the newest-only test can fail**

Change `latest_per_agent` to use `setdefault` on a list (returning all versions), re-run, confirm `test_only_the_newest_version_reaches_an_agent` FAILS. Restore in a `finally`; show `git diff --numstat`.

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/orchestrator2/context.py backend/tests/orchestrator2/test_context.py
git commit -m "feat(orchestrator2): hand-off context reads deliverables, newest version only

The run's *_artifacts columns are what the STANDALONE agents write. The
Orchestrator's agents share their names and capability and are a different thing;
reading those columns here would mix the two concepts the separate table exists to
keep apart.

Every version stays visible in the panel, but only the newest per agent is fed
forward - handing a downstream agent two versions of one PRD makes it guess.

ARTIFACT_COLUMNS and its import-time validation are deleted as dead. The guarantee
they stood for now lives in the agent_id CHECK-vs-AGENT_IDS drift guard."
```

---

### Task 6: The REST read

**Files:**
- Modify: `backend/shared/routers/runs.py` (after `get_run_artifacts`, ~line 240)
- Create: `frontend/app/api/runs/[id]/deliverables/route.ts`
- Test: `backend/tests/orchestrator2/test_deliverables_api.py`

**Interfaces:**
- Consumes: `deliverables_for_run`, `pointers_for_run` (Task 3); `_get_run_or_404` (existing, in `runs.py`)
- Produces: `GET /runs/{run_id}/deliverables` → `{"deliverables": [...]}`

- [ ] **Step 1: Write the failing test**

`backend/tests/orchestrator2/test_deliverables_api.py`:

```python
"""The deliverables read endpoint.

It is NEW ATTACK SURFACE, so it is tested for the access control its neighbours
have - not just for returning rows.
"""
import inspect

import pytest


def test_endpoint_is_registered():
    from shared.routers.runs import runs_router
    paths = {r.path for r in runs_router.routes}
    assert "/{run_id}/deliverables" in paths


def test_endpoint_is_tenant_scoped_through_get_run_or_404():
    """Its neighbours resolve the run through `_get_run_or_404`, which carries the
    tenant predicate. An endpoint that reads by run id alone would let any
    authenticated caller read another tenant's documents by guessing a uuid."""
    from shared.routers import runs
    src = inspect.getsource(runs.get_run_deliverables)
    assert "_get_run_or_404" in src
    assert "get_db_session_superuser" not in src, "that session BYPASSES row-level security"


def test_it_is_covered_by_the_route_protection_sweep():
    """`assert_all_routes_protected` skips WebSocket routes but covers REST ones, so
    this endpoint is guarded systemically rather than only by its own tests."""
    from shared.routers.runs import runs_router
    route = next(r for r in runs_router.routes if r.path == "/{run_id}/deliverables")
    assert route.dependencies or "request" in inspect.signature(
        route.endpoint).parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_api.py -v`
Expected: FAIL — `"/{run_id}/deliverables"` is not in the route set.

- [ ] **Step 3: Add the endpoint**

In `backend/shared/routers/runs.py`, immediately after `get_run_artifacts`:

```python
@runs_router.get("/{run_id}/deliverables")
async def get_run_deliverables(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Panel-ready deliverables for an Orchestrator run (reload/replay). Tenant-scoped.

    NOT `/artifacts`. That endpoint reads the run's `*_artifacts` columns, which the
    STANDALONE agents write and which carry an approval concept. The Orchestrator's
    agents are a different thing; their output lives in `orchestrator_deliverables`
    and is never gated.

    Resolved through `_get_run_or_404` like its neighbours, so the tenant predicate
    is not something this endpoint could forget on its own.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    from agents_orchestrator.orchestrator2.deliverables import (
        deliverables_for_run,
        pointers_for_run,
    )
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    dev_artifacts = getattr(run, "development_artifacts", None)

    # Which agents actually wrote files to disk. Checked HERE rather than inside
    # `pointers_for_run`, which stays pure: this is the only layer that can look at
    # the disk, and an agent that generated nothing must not be given an empty tree —
    # an empty tree reads as a pull that failed, not as a stage that produced no files.
    stages_with_files: set[str] = set()
    for agent_id in AGENT_IDS:
        if agent_id == "development":
            continue
        directory = await _run_stage_output_dir(
            str(run.id), agent_id,
            development_artifacts=dev_artifacts,
            tenant_id=str(tenant_id),
            project_id=str(run.project_id) if run.project_id else None,
        )
        if directory and os.path.isdir(directory) and os.listdir(directory):
            stages_with_files.add(agent_id)

    stored = await deliverables_for_run(str(run.id), str(tenant_id))
    pointers = pointers_for_run(dev_artifacts, stages_with_files)
    return {"deliverables": stored + pointers}
```

`os` is already imported at the top of `runs.py`.

- [ ] **Step 4: Add the BFF proxy**

`frontend/app/api/runs/[id]/deliverables/route.ts`:

```ts
import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Orchestrator deliverables read. Proxies FastAPI `GET /runs/{id}/deliverables`
 * → `{ deliverables: Deliverable[] }` so the panel repopulates on reload without
 * waiting on the WS.
 *
 * Deliberately distinct from the sibling `/artifacts` route: that one reads what the
 * STANDALONE agents wrote, which carries an approval concept the Orchestrator has
 * none of. Server-side JWT boundary is identical to the other `/runs/[id]/*` routes.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/runs/${encodeURIComponent(id)}/deliverables`, { session });
  return Response.json(data);
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && uv run python -m pytest tests/orchestrator2/test_deliverables_api.py -v
cd backend && uv run python -m pytest tests/test_route_protection.py -q   # the sweep must stay green
```

- [ ] **Step 6: Prove the tenant test can fail**

Replace `_get_run_or_404(db, run_id, tenant_id, request=request)` with a bare run lookup, re-run, confirm `test_endpoint_is_tenant_scoped_through_get_run_or_404` FAILS. Restore in a `finally`; show `git diff --numstat`.

- [ ] **Step 7: Commit**

```bash
git add backend/shared/routers/runs.py "frontend/app/api/runs/[id]/deliverables/route.ts" backend/tests/orchestrator2/test_deliverables_api.py
git commit -m "feat(orchestrator): GET /runs/{id}/deliverables, tenant-scoped

New read surface, so it carries the access control its neighbours have rather than
only its own tests: resolved through _get_run_or_404, which holds the tenant
predicate, and covered by the route-protection sweep.

Distinct from /artifacts on purpose - that endpoint reads what the standalone
agents wrote, which carries an approval concept the Orchestrator has none of."
```

---

### Task 7: The wire contract

**Files:**
- Create: `frontend/lib/orchestrator/deliverables.ts`
- Modify: `frontend/lib/orchestrator/protocol.ts`
- Test: `frontend/lib/orchestrator/__tests__/deliverables-contract.test.ts`
- Test: `backend/tests/orchestrator2/test_event_field_shapes.py`

**Interfaces:**
- Produces: `Deliverable` (Zod), `DeliverableReadyEvent` (Zod), `DELIVERABLE_EVENTS`, `DeliverablesRead`

- [ ] **Step 1: Write the failing tests**

`frontend/lib/orchestrator/__tests__/deliverables-contract.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { Deliverable, DeliverableReadyEvent } from "@/lib/orchestrator/deliverables";
import { OrchestratorEvent } from "@/lib/orchestrator/protocol";

/**
 * The union DROPS what fails `safeParse`. That has already cost two bugs on this
 * branch: an ErrorEvent.agent enum that guaranteed the "unknown agent" error could
 * never arrive, and a StreamChunkEvent.content that dropped Anthropic's block-list
 * content. So these tests check FIELD SHAPES, not just that the type is accepted.
 */
describe("the deliverable wire contract", () => {
  const frame = {
    type: "deliverable.ready",
    run_id: "r-1",
    agent: "security",
    deliverables: [{
      id: "d-1", agent: "security", kind: "markdown",
      title: "Security Report", content: "body",
      url: null, language: null, source: null,
      created_at: "2026-09-06T10:00:00+00:00",
    }],
  };

  it("accepts exactly what the backend emits", () => {
    const parsed = OrchestratorEvent.safeParse(frame);
    expect(parsed.success).toBe(true);
  });

  it("accepts the nullable columns the database actually returns", () => {
    // url/language/source are nullable columns. A `.optional()` that is not also
    // `.nullable()` rejects `null` — and a rejected frame is a DROPPED frame, which
    // on screen is indistinguishable from an agent that produced nothing.
    expect(Deliverable.safeParse({
      id: "d", agent: "plan", kind: "markdown", title: "t",
      content: "c", url: null, language: null, source: null, created_at: null,
    }).success).toBe(true);
  });

  it("accepts every one of the nine agents, including plan", () => {
    for (const agent of ["requirements", "design", "plan", "development",
      "code_review", "security", "testing", "deployment", "documentation"]) {
      expect(Deliverable.safeParse({
        id: "d", agent, kind: "markdown", title: "t", content: "c",
      }).success).toBe(true);
    }
  });

  it("rejects an unknown agent rather than rendering it under a blank heading", () => {
    expect(Deliverable.safeParse({
      id: "d", agent: "nonsense", kind: "markdown", title: "t", content: "c",
    }).success).toBe(false);
  });

  it("defaults a missing deliverables array rather than throwing", () => {
    const parsed = DeliverableReadyEvent.safeParse({
      type: "deliverable.ready", agent: "design",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) expect(parsed.data.deliverables).toEqual([]);
  });
});
```

`backend/tests/orchestrator2/test_event_field_shapes.py`:

```python
"""The FIELD shapes of the deliverable event, checked against the Zod schema.

`test_every_emitted_event_type_is_in_the_frontend_union` compares event TYPES only.
Both bugs this union has already produced were field-shape mismatches that passed
that test: the frame was the right type and was dropped anyway.
"""
import re
from pathlib import Path

SCHEMA = (
    Path(__file__).resolve().parents[3]
    / "frontend" / "lib" / "orchestrator" / "deliverables.ts"
)


def test_every_field_the_backend_emits_exists_in_the_zod_schema():
    from agents_orchestrator.orchestrator2.deliverables import _to_wire

    class _Row:
        id = "x"; run_id = "r"; tenant_id = "t"; project_id = None
        agent_id = "security"; kind = "markdown"; title = "T"
        content = "c"; url = None; language = None; source = None
        created_at = None

    emitted = set(_to_wire(_Row()))
    schema = SCHEMA.read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s{2}([a-z_]+):\s*z\.", schema, re.M))
    missing = emitted - declared
    assert not missing, (
        f"the backend emits {sorted(missing)}, which the Zod schema does not declare - "
        "such a frame is validated, rejected and SILENTLY DROPPED"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npx vitest run lib/orchestrator/__tests__/deliverables-contract.test.ts
cd backend && uv run python -m pytest tests/orchestrator2/test_event_field_shapes.py -v
```
Expected: both FAIL — `lib/orchestrator/deliverables.ts` does not exist.

- [ ] **Step 3: Write the schema**

`frontend/lib/orchestrator/deliverables.ts`:

```ts
import { z } from "zod";

import { ArtifactKind } from "@/lib/copilot/artifacts";
import { OrchestratorAgentId } from "@/lib/orchestrator/protocol";

/**
 * Deliverables — what the Orchestrator's agents produce.
 *
 * NOT the `Artifact` the standalone agents write. That one carries an approval
 * status because a delivery role produced it and somebody accepts it; the
 * Orchestrator is Project-Admin-only and has no gates, so its output is a separate
 * concept with a separate table and no approval anywhere in its shape.
 *
 * Owned by `lib/orchestrator/`, not imported from `lib/copilot/`, which Phase 5
 * deletes. `ArtifactKind` is the one exception: it is the renderer registry's
 * vocabulary, shared by both surfaces, and moves here whole when the Copilot goes.
 *
 * NULLABLE, NOT MERELY OPTIONAL. `url`, `language` and `source` are nullable
 * database columns and arrive as `null`. A schema that accepts `undefined` but not
 * `null` rejects the frame — and a rejected frame is DROPPED, which on screen is
 * indistinguishable from an agent that produced nothing.
 */
export const Deliverable = z.object({
  id: z.string(),
  agent: OrchestratorAgentId,
  kind: ArtifactKind,
  title: z.string(),
  content: z.string().nullish().default(""),
  url: z.string().nullish(),
  language: z.string().nullish(),
  source: z.string().nullish(),
  /** ISO-8601. Null for synthesized pointers, which have no single moment. */
  created_at: z.string().nullish(),
});
export type Deliverable = z.infer<typeof Deliverable>;

export const DeliverableReadyEvent = z.object({
  type: z.literal("deliverable.ready"),
  run_id: z.string().optional(),
  agent: OrchestratorAgentId,
  deliverables: z.array(Deliverable).default([]),
});
export type DeliverableReadyEvent = z.infer<typeof DeliverableReadyEvent>;

/** Ready to fold into the Orchestrator event union. */
export const DELIVERABLE_EVENTS = [DeliverableReadyEvent] as const;

/** `GET /api/runs/[id]/deliverables` → `{ deliverables: Deliverable[] }`. */
export const DeliverablesRead = z.object({
  deliverables: z.array(Deliverable).default([]),
});
export type DeliverablesRead = z.infer<typeof DeliverablesRead>;
```

- [ ] **Step 4: Fold it into the union**

In `frontend/lib/orchestrator/protocol.ts`, add the import and extend the union:

```ts
import { DELIVERABLE_EVENTS } from "@/lib/orchestrator/deliverables";
```
```ts
export const OrchestratorEvent = z.discriminatedUnion("type", [
  StreamChunkEvent,
  StreamEndEvent,
  AgentSelectedEvent,
  ToolCallEvent,
  ThinkingEvent,
  ChoiceCardEvent,
  ErrorEvent,
  ...ARTIFACT_EVENTS,
  ...DELIVERABLE_EVENTS,
]);
```

`deliverables.ts` imports `OrchestratorAgentId` from `protocol.ts` and `protocol.ts`
imports `DELIVERABLE_EVENTS` back — a cycle ES modules resolve, but only because the
`OrchestratorAgentId` const is initialised before the union is built. If the
`OrchestratorEvent` union throws at import, move `OrchestratorAgentId` and
`ORCHESTRATOR_AGENT_IDS` into their own `lib/orchestrator/agents.ts` and import it
from both. Verify by running the test suite, not by reasoning about it.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd frontend && npx vitest run lib/orchestrator/
cd frontend && npm run typecheck
cd backend && uv run python -m pytest tests/orchestrator2/test_event_field_shapes.py -v
```

- [ ] **Step 6: Prove the field-shape test can fail**

Delete the `source: z.string().nullish(),` line from `deliverables.ts`, re-run the backend test, confirm `test_every_field_the_backend_emits_exists_in_the_zod_schema` FAILS naming `source`. Restore in a `finally`; show `git diff --numstat`.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/orchestrator/deliverables.ts frontend/lib/orchestrator/protocol.ts frontend/lib/orchestrator/__tests__/deliverables-contract.test.ts backend/tests/orchestrator2/test_event_field_shapes.py
git commit -m "feat(orchestrator): the Deliverable wire contract, owned by lib/orchestrator

Not imported from lib/copilot, which Phase 5 deletes.

Fields are nullish, not merely optional: url/language/source are nullable columns
and arrive as null, and a schema that rejects null drops the frame - which on
screen is indistinguishable from an agent that produced nothing. That exact shape
has already cost this union two bugs, so the new test compares FIELD SHAPES and not
only event types, which is all the existing sweep checked."
```

---

### Task 8: The socket accumulates deliverables

**Files:**
- Modify: `frontend/lib/orchestrator/use-orchestrator-socket.ts`
- Test: `frontend/lib/orchestrator/__tests__/use-orchestrator-socket.test.tsx`

**Interfaces:**
- Consumes: `Deliverable` (Task 7)
- Produces: `deliverables: Deliverable[]` on `UseOrchestratorSocketResult`, newest first

- [ ] **Step 1: Write the failing test**

Append to `frontend/lib/orchestrator/__tests__/use-orchestrator-socket.test.tsx`, following the existing harness in that file for opening a socket and pushing frames:

```tsx
describe("deliverables", () => {
  const row = (id: string, agent: string, title: string) => ({
    id, agent, kind: "markdown", title, content: "body",
    url: null, language: null, source: null,
    created_at: "2026-09-06T10:00:00+00:00",
  });

  it("collects what an agent produced", async () => {
    const { result, push } = await openSocket();
    push({ type: "deliverable.ready", agent: "security",
           deliverables: [row("d1", "security", "Security Report")] });
    await waitFor(() => expect(result.current.deliverables).toHaveLength(1));
    expect(result.current.deliverables[0].title).toBe("Security Report");
  });

  it("keeps every version, newest first", async () => {
    // A re-run must never destroy the document it replaces - that is the whole
    // reason deliverables are append-only.
    const { result, push } = await openSocket();
    push({ type: "deliverable.ready", agent: "security",
           deliverables: [row("d1", "security", "first")] });
    push({ type: "deliverable.ready", agent: "security",
           deliverables: [row("d2", "security", "second")] });
    await waitFor(() => expect(result.current.deliverables).toHaveLength(2));
    expect(result.current.deliverables.map((d) => d.title)).toEqual(["second", "first"]);
  });

  it("does not duplicate a deliverable that arrives twice", async () => {
    // A reconnect can replay a frame. Ids are stable, so de-dupe on id.
    const { result, push } = await openSocket();
    push({ type: "deliverable.ready", agent: "design",
           deliverables: [row("d1", "design", "HLD")] });
    push({ type: "deliverable.ready", agent: "design",
           deliverables: [row("d1", "design", "HLD")] });
    await waitFor(() => expect(result.current.deliverables).toHaveLength(1));
  });

  it("drops the transcript's deliverables on reset", async () => {
    const { result, push } = await openSocket();
    push({ type: "deliverable.ready", agent: "plan",
           deliverables: [row("d1", "plan", "Sprint plan")] });
    await waitFor(() => expect(result.current.deliverables).toHaveLength(1));
    act(() => result.current.reset());
    // Switching project repoints the session in place while keeping its id. The
    // Phase 3 review found the run surviving that switch and the next turn spending
    // the OLD project's budget; deliverables must not survive it either.
    await waitFor(() => expect(result.current.deliverables).toHaveLength(0));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/use-orchestrator-socket.test.tsx`
Expected: FAIL — `result.current.deliverables` is `undefined`.

- [ ] **Step 3: Implement accumulation**

Add the state alongside `activity`:

```tsx
  // Every version, newest first. Append-only, de-duped on id: a reconnect can
  // replay a frame, and the same agent running twice must produce two rows rather
  // than one overwriting the other.
  const [deliverables, setDeliverables] = React.useState<Deliverable[]>([]);
```

Replace the dead artifact-event block with:

```tsx
        case "deliverable.ready": {
          const incoming = evt.deliverables ?? [];
          if (incoming.length) {
            setDeliverables((prev) => {
              const seen = new Set(prev.map((d) => d.id));
              const fresh = incoming.filter((d) => !seen.has(d.id));
              return fresh.length ? [...fresh, ...prev] : prev;
            });
          }
          break;
        }
        // The Copilot's streaming artifact events. Declared by the union (it still
        // folds in ARTIFACT_EVENTS) and never emitted by this engine — deliverables
        // arrive whole, in one `deliverable.ready`, because text already streams to
        // chat and a second streaming channel would carry nothing new. Named here as
        // unreachable rather than left looking like a feature with no data behind it.
        case "artifact.open":
        case "artifact.delta":
        case "artifact.end":
        case "artifact.ready":
          break;
```

Clear it in the same place `reset()` clears the transcript, add `deliverables` to the returned object, and add `deliverables: Deliverable[];` to `UseOrchestratorSocketResult` with a docstring saying every version is kept newest-first.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run lib/orchestrator/
cd frontend && npm run typecheck
```

- [ ] **Step 5: Prove the append test can fail**

Change the reducer to `return incoming` (last-wins), re-run, confirm `keeps every version, newest first` FAILS. Restore in a `finally`; show `git diff --numstat`.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/orchestrator/use-orchestrator-socket.ts frontend/lib/orchestrator/__tests__/use-orchestrator-socket.test.tsx
git commit -m "feat(orchestrator): the socket collects deliverables, newest first

Append-only and de-duped on id: the same agent running twice produces two rows
rather than one overwriting the other, and a reconnect that replays a frame does
not double it.

Cleared by reset() alongside the transcript. Switching project repoints a session
in place while keeping its id, and the Phase 3 review found the run surviving that
switch; deliverables must not survive it either."
```

---

### Task 9: The panel shows them

**Files:**
- Modify: `frontend/components/orchestrator/cockpit.tsx:514-519`
- Modify: `frontend/components/orchestrator/artifacts-panel.tsx`
- Test: `frontend/components/orchestrator/__tests__/deliverables-panel.test.tsx`

**Interfaces:**
- Consumes: `deliverables` from the socket (Task 8); `DeliverablesRead` (Task 7)
- Produces: `ArtifactsPanelProps.tabLabel?: string` (default `"Artifacts"`)

- [ ] **Step 1: Write the failing test**

`frontend/components/orchestrator/__tests__/deliverables-panel.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";

/**
 * The panel is SHARED with the still-live Copilot. Every change here must leave the
 * Copilot reading "Artifacts" with its approval affordances intact — Phase 5 deletes
 * it, and until then breaking it is a live regression.
 */
const base = {
  runId: "run-1", gate: null, openArtifactId: null,
  onSelectArtifact: () => {}, streamingArtifactId: null,
  collapsed: false, onToggle: () => {},
};

const art = (id: string, stage: string, title: string, at?: string) => ({
  id, stage, kind: "markdown" as const, title, content: "body", created_at: at,
});

describe("the deliverables panel", () => {
  it("labels the tab Artifacts by default, so the Copilot is untouched", () => {
    render(<ArtifactsPanel {...base} activeStage="design" artifacts={[]} />);
    expect(screen.getByRole("button", { name: /artifacts/i })).toBeInTheDocument();
  });

  it("labels the tab Deliverables when asked", () => {
    render(<ArtifactsPanel {...base} activeStage="design" artifacts={[]} tabLabel="Deliverables" />);
    expect(screen.getByRole("button", { name: /deliverables/i })).toBeInTheDocument();
  });

  it("groups agent-wise", () => {
    render(<ArtifactsPanel {...base} activeStage="security" tabLabel="Deliverables"
      artifacts={[art("a", "security", "Security Report"), art("b", "requirements", "PRD")]} />);
    expect(screen.getByText("Security")).toBeInTheDocument();
    expect(screen.getByText("Requirements")).toBeInTheDocument();
  });

  it("shows the Project Manager agent by that name, never 'Plan'", () => {
    render(<ArtifactsPanel {...base} activeStage="plan" tabLabel="Deliverables"
      artifacts={[art("a", "plan", "Sprint plan")]} />);
    expect(screen.getByText(/project manager/i)).toBeInTheDocument();
  });

  it("orders versions newest first within an agent", () => {
    render(<ArtifactsPanel {...base} activeStage="security" tabLabel="Deliverables"
      artifacts={[
        art("old", "security", "Security Report", "2026-09-06T10:00:00Z"),
        art("new", "security", "Security Report", "2026-09-06T14:00:00Z"),
      ]} />);
    const group = screen.getByTestId("deliverable-group-security");
    const titles = within(group).getAllByTestId("deliverable-title");
    expect(titles[0]).toHaveAttribute("data-id", "new");
  });

  it("distinguishes two versions by timestamp, so the reader can tell them apart", () => {
    render(<ArtifactsPanel {...base} activeStage="security" tabLabel="Deliverables"
      artifacts={[
        art("old", "security", "Security Report", "2026-09-06T10:00:00Z"),
        art("new", "security", "Security Report", "2026-09-06T14:00:00Z"),
      ]} />);
    const group = screen.getByTestId("deliverable-group-security");
    expect(within(group).getAllByTestId("deliverable-time")).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/orchestrator/__tests__/deliverables-panel.test.tsx`
Expected: FAIL — no `tabLabel` prop, no `deliverable-group-*` test ids.

- [ ] **Step 3: Implement the panel changes**

In `artifacts-panel.tsx`:

1. Add to `ArtifactsPanelProps`:

```tsx
  /**
   * What the first tab is called. Defaults to `"Artifacts"` so the still-live
   * Copilot is unchanged; the Orchestrator passes `"Deliverables"`, because what its
   * agents produce is a separate concept from the approval-gated artifacts the
   * standalone agents write.
   */
  tabLabel?: string;
```

2. Add `created_at?: string | null` to the `Artifact` type used by the panel (in `lib/copilot/artifacts.ts`, as `z.string().nullish()`), so a version can be labelled.

3. Replace the three hardcoded `Artifacts` strings (the collapsed rail at ~line 318, the tab button at ~line 384, and the `aria-label` at ~line 310) with `tabLabel`, defaulted in the destructure: `tabLabel = "Artifacts"`.

4. In `groupByStage`, sort each group newest-first before returning:

```tsx
    .map(([stage, items]) => ({
      stage,
      // Newest first. Every version of a document is kept, so without this the
      // reader has to work out which of three identically-titled reports is current.
      items: [...items].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "")),
    }));
```

5. In the group renderer, add `data-testid={`deliverable-group-${stage}`}` to the group container, `data-testid="deliverable-title" data-id={a.id}` to each row's title element, and render a timestamp next to it when `created_at` is set:

```tsx
{a.created_at && (
  <span data-testid="deliverable-time" className="text-muted-foreground font-mono text-[10px]">
    {new Date(a.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
  </span>
)}
```

In `cockpit.tsx`, replace lines 514-519:

```tsx
            <ArtifactsPanel
              // The real run, not "". The panel resolves the Development code tree
              // and every per-agent file tree against this id, so an empty string
              // rendered a permanently empty tree that looked like a repo the agent
              // had failed to pull.
              runId={runIdRef.current ?? ""}
              // Drives which group is expanded AND the Development code tree, which
              // the panel synthesises only while Development is the active agent.
              activeStage={socket.activeAgent ?? ""}
              gate={null}
              showApprover={false}
              tabLabel="Deliverables"
              artifacts={deliverables}
              openArtifactId={openDeliverableId}
              onSelectArtifact={setOpenDeliverableId}
              streamingArtifactId={null}
```

Add above the return, mapping the wire shape to the panel's (D17 — adapt at the boundary):

```tsx
  const [openDeliverableId, setOpenDeliverableId] = React.useState<string | null>(null);

  // Replay what the run already holds, so reopening a conversation shows its
  // documents without waiting for another turn.
  const deliverablesQ = useQuery({
    queryKey: ["orchestrator", "deliverables", runIdRef.current],
    queryFn: async () => {
      const res = await fetch(`/api/runs/${encodeURIComponent(runIdRef.current!)}/deliverables`,
        { credentials: "include" });
      return DeliverablesRead.parse(await res.json());
    },
    enabled: !!runIdRef.current,
  });

  // The panel speaks `stage`; the wire speaks `agent`. One mapping, at the boundary
  // — the panel is shared with the Copilot and must not learn a second vocabulary.
  const deliverables = React.useMemo(() => {
    const seen = new Set<string>();
    return [...socket.deliverables, ...(deliverablesQ.data?.deliverables ?? [])]
      .filter((d) => (seen.has(d.id) ? false : (seen.add(d.id), true)))
      .map((d) => ({
        id: d.id, stage: d.agent, kind: d.kind, title: d.title,
        content: d.content ?? "", url: d.url ?? undefined,
        language: d.language ?? undefined, source: d.source ?? undefined,
        created_at: d.created_at ?? undefined,
      }));
  }, [socket.deliverables, deliverablesQ.data]);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npx vitest run components/orchestrator/ lib/orchestrator/
cd frontend && npm run typecheck
cd frontend && npx eslint components/orchestrator lib/orchestrator
```

- [ ] **Step 5: Confirm the Copilot is unbroken**

```bash
cd frontend && npx vitest run components/copilot/ components/orchestrator/__tests__/artifacts-panel-approver.test.tsx
```
Expected: green, with the Copilot still reading "Artifacts".

- [ ] **Step 6: Prove two tests can fail**

Change the `tabLabel` default to `"Deliverables"`, confirm `labels the tab Artifacts by default` FAILS. Restore. Then remove the sort from `groupByStage`, confirm `orders versions newest first` FAILS. Restore in a `finally`; show `git diff --numstat` for each.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/orchestrator/cockpit.tsx frontend/components/orchestrator/artifacts-panel.tsx frontend/lib/copilot/artifacts.ts frontend/components/orchestrator/__tests__/deliverables-panel.test.tsx
git commit -m "feat(orchestrator): the Deliverables tab shows what the agents produced

runId was the empty string and artifacts a literal [], so the panel rendered an
empty tab and a permanently empty code tree - indistinguishable from an agent that
produced nothing and a repo it had failed to pull. activeStage was likewise \"\",
which is why the Development code tree never appeared at all.

The panel is shared with the still-live Copilot, so the tab name is a prop
defaulting to Artifacts rather than a rename, and the wire's `agent` is mapped to
its `stage` at the boundary rather than teaching it a second vocabulary."
```

---

## Verification

- [ ] `cd backend && uv run python -m pytest tests/orchestrator2/ -q` — all green
- [ ] `cd backend && uv run python -m pytest tests/ -q` — no regression outside `tests/test_m7_rbac.py`, whose two cross-tenant failures are **known and must stay red** until a non-superuser application role exists (carried debt 1)
- [ ] `cd frontend && npx vitest run` — all green
- [ ] `cd frontend && npm run typecheck` — clean
- [ ] `cd frontend && npx eslint components/orchestrator lib/orchestrator` — clean
- [ ] `cd backend && uv run python scripts/live_routing_check.py` — still 28/29; routing is untouched by this phase, so a change here means something regressed
- [ ] **Live check** with Docker up, backend on **8004** (`frontend/.env.local` sets `FASTAPI_INTERNAL_URL=http://localhost:8004`; the docs saying 8001 are stale) and frontend on 3000: sign in as `sarthakk2004@gmail.com` (the only `project_admin`), open the "reall" project's Orchestrator, ask for a PRD, and confirm the document appears under a **Requirements** heading in the **Deliverables** tab. Then ask for it again and confirm **two** versions appear, newest first.
- [ ] Whole-branch review by an agent that did not implement these tasks

## Out of scope

Streaming documents into the panel live (D15); promoting a Deliverable into the project `artifacts` library; the context truncation budget (carried debt 6, still needs a product decision); retiring the old engines (Phase 5); the e2e rewrite deferred in Phase 1.
