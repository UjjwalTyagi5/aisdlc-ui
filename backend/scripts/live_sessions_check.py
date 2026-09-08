"""Orchestrator chat history against the REAL database, with the seeded dev tenant.

Every Phase 5B test fakes the conversation service. That proves the calls are made with
the right arguments and says nothing about whether Postgres accepts the rows, whether
the session actually appears in the rail's list query, or whether a reopened chat reads
its transcript back. This closes that gap — the same one `live_deliverables_check.py`
closes for Deliverables.

Run from `backend/`:  uv run python scripts/live_sessions_check.py
"""
import asyncio
import sys
import uuid

TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "c3b0cd34-6657-4f91-b1f2-04f394502f81"
USER = "af57932d-f4f2-4673-aef7-d33b75c022f4"   # sarthakk2004@gmail.com, project admin
OTHER_USER = "902a8e2a-3c7c-4212-b8ea-20d530d91081"

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
    from sqlalchemy import delete

    import shared.db as shared_db
    from agents_orchestrator.orchestrator2 import sessions
    from shared.models.orm import ConversationMessage, ConversationSession, Run
    from shared.services import conversation_service as cs

    run_id = str(uuid.uuid4())

    async with shared_db.get_db_session_for_tenant(TENANT) as s:
        s.add(Run(id=uuid.UUID(run_id), project_id=uuid.UUID(PROJECT),
                  tenant_id=uuid.UUID(TENANT), stage="requirements", status="running"))
        await s.commit()
    print(f"\n  run {run_id}\n")

    try:
        # ── a chat is born from its first turn ──
        await sessions.ensure_session(
            run_id, tenant_id=TENANT, project_id=PROJECT, user_id=USER,
            first_message="I need a PRD for the billing rework",
        )
        await sessions.record_turn(run_id, "user", "I need a PRD for the billing rework",
                                   tenant_id=TENANT, user_id=USER)
        await sessions.record_turn(run_id, "agent", "Here is the PRD you asked for.",
                                   tenant_id=TENANT, user_id=USER)

        # ── it appears in the rail's list ──
        listed = await cs.list_sessions(
            TENANT, created_by=USER, agent_id=sessions.ORCHESTRATOR_AGENT_KEY,
            project_id=uuid.UUID(PROJECT),
        )
        mine = [r for r in listed if r.get("id") == run_id]
        check("the chat appears in this person's rail", len(mine) == 1,
              f"got {len(mine)} of {len(listed)}")
        if mine:
            check("labelled with what was actually asked",
                  "billing" in (mine[0].get("title") or "").lower(),
                  repr(mine[0].get("title")))

        # ── and reads back as a transcript ──
        rows = await cs.get_transcript(run_id, tenant_id=TENANT)
        check("both sides of the turn are stored", len(rows) == 2, f"got {len(rows)}")
        check("in the order they were said",
              [r.get("role") for r in rows][:2] in (["user", "agent"], ["user", "assistant"]),
              str([r.get("role") for r in rows]))
        check("the agent's words survive intact",
              any("Here is the PRD" in (r.get("content") or "") for r in rows))

        # ── ensure_session is idempotent, because it runs on every turn ──
        await sessions.ensure_session(
            run_id, tenant_id=TENANT, project_id=PROJECT, user_id=USER,
            first_message="a different first message",
        )
        listed2 = await cs.list_sessions(
            TENANT, created_by=USER, agent_id=sessions.ORCHESTRATOR_AGENT_KEY,
            project_id=uuid.UUID(PROJECT),
        )
        again = [r for r in listed2 if r.get("id") == run_id]
        check("calling it again makes no second chat", len(again) == 1, str(len(again)))
        if again:
            check("and does not relabel the existing one",
                  "billing" in (again[0].get("title") or "").lower(),
                  repr(again[0].get("title")))

        # ── another person does not see it ──
        theirs = await cs.list_sessions(
            TENANT, created_by=OTHER_USER, agent_id=sessions.ORCHESTRATOR_AGENT_KEY,
            project_id=uuid.UUID(PROJECT),
        )
        check("another person's rail does not show it",
              not any(r.get("id") == run_id for r in theirs))

        # ── and it is filed under the Orchestrator, not one of the nine ──
        as_requirements = await cs.list_sessions(
            TENANT, created_by=USER, agent_id="requirements",
            project_id=uuid.UUID(PROJECT),
        )
        check("it does not leak into a standalone agent's history",
              not any(r.get("id") == run_id for r in as_requirements))

        # ── session id IS run id, which is what makes continuing work ──
        async with shared_db.get_db_session_for_tenant(TENANT) as s:
            row = await s.get(ConversationSession, uuid.UUID(run_id))
            check("the session id is the run id", row is not None)
            if row is not None:
                check("and it points back at the run", str(row.run_id) == run_id,
                      str(row.run_id))
                check("filed under the orchestrator",
                      row.agent_id == sessions.ORCHESTRATOR_AGENT_KEY, str(row.agent_id))

    finally:
        async with shared_db.get_db_session_for_tenant(TENANT) as s:
            await s.execute(delete(ConversationMessage).where(
                ConversationMessage.session_id == uuid.UUID(run_id)))
            await s.execute(delete(ConversationSession).where(
                ConversationSession.id == uuid.UUID(run_id)))
            await s.execute(delete(Run).where(Run.id == uuid.UUID(run_id)))
            await s.commit()
        print("\n  cleaned up")

    print(f"\n  {ok} passed, {fail} failed\n")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
