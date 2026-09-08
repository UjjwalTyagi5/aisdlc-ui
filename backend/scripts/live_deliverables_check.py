"""Deliverables against the REAL database, with the seeded dev tenant.

Every test written for Phase 4 fakes the session. That proves the SQL is assembled
correctly and says nothing about whether the table accepts the write, whether the
CHECK constraints match what the code emits, or whether the tenant GUC session can
read its own rows back. This closes that gap.

Run from `backend/`:  uv run python <this file>
"""
import asyncio
import sys
import uuid

TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "c3b0cd34-6657-4f91-b1f2-04f394502f81"
OTHER_TENANT = "99999999-9999-4999-8999-999999999999"

ok = fail = 0


def check(label, condition, detail=""):
    global ok, fail
    if condition:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}  {detail}")


async def main() -> int:
    print("interpreter:", sys.executable)
    from sqlalchemy import delete, select

    import shared.db as shared_db
    from agents_orchestrator.orchestrator2 import deliverables as dv
    from shared.models.orm import OrchestratorDeliverable, Run

    run_id = str(uuid.uuid4())

    # A real run row, so the FK holds.
    async with shared_db.get_db_session_for_tenant(TENANT) as s:
        s.add(Run(id=uuid.UUID(run_id), project_id=uuid.UUID(PROJECT),
                  tenant_id=uuid.UUID(TENANT), stage="requirements", status="running"))
        await s.commit()
    print(f"\n  run {run_id}\n")

    try:
        # ── write two versions of one agent's document, plus another agent ──
        v1 = await dv.capture("requirements", "# PRD v1\n" + "a" * 400,
                              run_id=run_id, tenant_id=TENANT, project_id=PROJECT)
        await asyncio.sleep(0.05)  # distinct created_at
        v2 = await dv.capture("requirements", "# PRD v2\n" + "b" * 400,
                              run_id=run_id, tenant_id=TENANT, project_id=PROJECT)
        pm = await dv.capture("plan", "# Sprint plan\n" + "c" * 400,
                              run_id=run_id, tenant_id=TENANT, project_id=PROJECT)

        check("a write succeeds against the real table", len(v1) == 1 and len(v2) == 1)
        check("the Project Manager agent's output persists like any other", len(pm) == 1)
        check("two versions get different ids", v1[0]["id"] != v2[0]["id"])
        check("the title comes from the document's own heading",
              v2[0]["title"] == "PRD v2", v2[0]["title"])

        # ── read back, newest first ──
        rows = await dv.deliverables_for_run(run_id, TENANT)
        check("all three versions are readable", len(rows) == 3, f"got {len(rows)}")
        check("newest first", rows[0]["title"] in ("Sprint plan", "PRD v2"),
              str([r["title"] for r in rows]))
        titles = [r["title"] for r in rows]
        check("NOTHING was overwritten — v1 survives the re-run", "PRD v1" in titles)

        # ── latest per agent ──
        latest = await dv.latest_per_agent(run_id, TENANT)
        check("latest_per_agent covers both agents",
              set(latest) == {"requirements", "plan"}, str(sorted(latest)))
        check("only the NEWEST requirements version feeds an agent",
              latest["requirements"]["title"] == "PRD v2",
              latest["requirements"]["title"])

        # ── the context an agent actually receives ──
        from agents_orchestrator.orchestrator2.context import handoff_context
        ctx = await handoff_context(run_id, TENANT, "design")
        check("the hand-off context carries the newest PRD", "PRD v2" in ctx)
        check("the hand-off context does NOT carry the superseded one",
              "PRD v1" not in ctx)
        check("the Project Manager agent's work reaches the next agent",
              "Sprint plan" in ctx)
        check("the document arrives as markdown, not JSON-quoted",
              chr(92) + "n" not in ctx)

        # ── tenant isolation, for real ──
        stolen = await dv.deliverables_for_run(run_id, OTHER_TENANT)
        check("another tenant reads nothing", stolen == [], str(stolen))

        # ── the CHECK constraints actually bite ──
        try:
            async with shared_db.get_db_session_for_tenant(TENANT) as s:
                s.add(OrchestratorDeliverable(
                    id=uuid.uuid4(), run_id=uuid.UUID(run_id),
                    tenant_id=uuid.UUID(TENANT), project_id=None,
                    agent_id="marketing", kind="markdown", title="x", content="y"))
                await s.commit()
            check("the database refuses an agent id outside the nine", False,
                  "the row was accepted")
        except Exception:
            check("the database refuses an agent id outside the nine", True)

        # ── no approval concept exists on this table ──
        async with shared_db.get_db_session_for_tenant(TENANT) as s:
            cols = set(OrchestratorDeliverable.__table__.columns.keys())
        check("no approval column anywhere in the shape",
              not {"approval_status", "approved_by", "approved_at"} & cols)

    finally:
        async with shared_db.get_db_session_for_tenant(TENANT) as s:
            await s.execute(delete(OrchestratorDeliverable).where(
                OrchestratorDeliverable.run_id == uuid.UUID(run_id)))
            await s.execute(delete(Run).where(Run.id == uuid.UUID(run_id)))
            await s.commit()
        print("\n  cleaned up")

    print(f"\n  {ok} passed, {fail} failed\n")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
