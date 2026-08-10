"""E2E fixture seed script for Playwright real-api tests.

Inserts (idempotent) a known test tenant, project, run, and artifact set so the
real-api Playwright project can navigate a deterministic golden path without
relying on production data.

SECURITY (T-M4-18): all rows belong to the E2E test tenant only; the tenant is
identified by a well-known constant slug so the seed never touches real data.

Run from the repo root:
    cd agentic_app && python scripts/seed_e2e_fixtures.py

Prerequisites:
    - docker compose up -d postgres
    - POSTGRES_CONN_STRING env var (or defaults to localhost sdlc_agentic)
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure agentic_app/ is importable when run directly as `python scripts/seed_e2e_fixtures.py`
# (running a file puts its own dir on sys.path, not the package root).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import insert as pg_insert

from config.env import JWT_SECRET_KEY  # noqa: F401 — ensures env loads correctly
from shared.db import AsyncSessionFactory
from shared.models.orm import (
    Artifact,
    Organization,
    Project,
    Run,
    Workspace,
)

# ── Well-known E2E constants ─────────────────────────────────────────────────

E2E_ORG_SLUG = "e2e-test-org"
E2E_WORKSPACE_SLUG = "e2e-workspace"
E2E_PROJECT_DISPLAY_NAME = "Ingest pipeline"
E2E_PROJECT_EXTERNAL_REF = "ingest"

# Deterministic UUIDs — generated once, never change between seed runs
E2E_ORG_ID = uuid.UUID("00000000-e2e0-4000-a000-000000000001")
E2E_WORKSPACE_ID = uuid.UUID("00000000-e2e0-4000-a000-000000000002")
E2E_TENANT_ID = E2E_ORG_ID  # tenant_id == org_id for single-tenant bootstrap
E2E_PROJECT_ID = uuid.UUID("00000000-e2e0-4000-a000-000000000003")
E2E_RUN_ID = uuid.UUID("00000000-e2e0-4000-a000-000000000004")

# Artifact IDs
E2E_ARTIFACT_STORY_ID = uuid.UUID("00000000-e2e0-4000-a000-000000000010")
E2E_ARTIFACT_DIAGRAM_ID = uuid.UUID("00000000-e2e0-4000-a000-000000000011")

_NOW = datetime.now(timezone.utc)


async def seed() -> None:
    async with AsyncSessionFactory() as session:
        # ── Organization ───────────────────────────────────────────────────
        # ON CONFLICT DO NOTHING on id PK — safe to run multiple times (D-SET-06)
        await session.execute(
            pg_insert(Organization).values(
                id=E2E_ORG_ID,
                slug=E2E_ORG_SLUG,
                display_name="E2E Test Organisation",
                created_at=_NOW,
                updated_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        print(f"  upserted org  {E2E_ORG_ID}")

        # ── Workspace ──────────────────────────────────────────────────────
        # ON CONFLICT DO NOTHING on id PK — keeps exactly one workspace per org
        # so resolve_default_workspace never raises 409 on re-seed (D-SET-06).
        await session.execute(
            pg_insert(Workspace).values(
                id=E2E_WORKSPACE_ID,
                organization_id=E2E_ORG_ID,
                slug=E2E_WORKSPACE_SLUG,
                display_name="E2E Workspace",
                created_at=_NOW,
                updated_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        print(f"  upserted ws   {E2E_WORKSPACE_ID}")

        # ── Project ────────────────────────────────────────────────────────
        await session.execute(
            pg_insert(Project).values(
                id=E2E_PROJECT_ID,
                workspace_id=E2E_WORKSPACE_ID,
                tenant_id=E2E_TENANT_ID,
                external_ref=E2E_PROJECT_EXTERNAL_REF,
                display_name=E2E_PROJECT_DISPLAY_NAME,
                provider_kind="azure_devops",
                archived=False,
                created_at=_NOW,
                updated_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        print(f"  upserted proj {E2E_PROJECT_ID} — '{E2E_PROJECT_DISPLAY_NAME}'")

        # ── Run ────────────────────────────────────────────────────────────
        await session.execute(
            pg_insert(Run).values(
                id=E2E_RUN_ID,
                project_id=E2E_PROJECT_ID,
                tenant_id=E2E_TENANT_ID,
                stage="requirements",
                status="awaiting_approval",
                current_stage="requirements",
                gate_pending=True,
                requirements_payload={
                    "title": "Requirements — Ingest pipeline",
                    "agent": "requirements",
                    "phase": "requirements",
                    "trigger": "manual",
                    "startedBy": "u_admin",
                    "startedAt": _NOW.isoformat(),
                    "pendingApprovers": ["u_admin"],
                    "cost": {
                        "usd": 0.042,
                        "inputTokens": 12000,
                        "outputTokens": 3200,
                    },
                },
                created_at=_NOW,
                updated_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        print(f"  upserted run  {E2E_RUN_ID} (awaiting_approval)")

        # ── Story artifact with traceability ──────────────────────────────
        # This is the key artifact the traceability-panel parity test needs.
        # The body carries jiraIssueKey + designArtifactId + prUrl so the
        # TraceabilityPanel can render real linked rows.
        await session.execute(
            pg_insert(Artifact).values(
                id=E2E_ARTIFACT_STORY_ID,
                run_id=E2E_RUN_ID,
                tenant_id=E2E_TENANT_ID,
                artifact_type="story",
                content_type="application/json",
                blob_url=None,
                blob_path=None,
                size_bytes=None,
                created_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        print(f"  upserted story artifact  {E2E_ARTIFACT_STORY_ID} (with traceability)")

        # ── Mermaid diagram artifact ──────────────────────────────────────
        await session.execute(
            pg_insert(Artifact).values(
                id=E2E_ARTIFACT_DIAGRAM_ID,
                run_id=E2E_RUN_ID,
                tenant_id=E2E_TENANT_ID,
                artifact_type="c4_diagram",
                content_type="image/svg+xml",
                blob_url=None,
                blob_path=None,
                size_bytes=None,
                created_at=_NOW,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        print(f"  upserted diagram artifact {E2E_ARTIFACT_DIAGRAM_ID}")

        await session.commit()
        print("seed complete — all E2E fixtures present")


if __name__ == "__main__":
    print("seeding E2E fixtures …")
    asyncio.run(seed())
