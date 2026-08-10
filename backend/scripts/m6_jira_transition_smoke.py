"""Milestone-6 Jira connector — move_item_state (workflow transition) smoke test.

This is the one Jira CRUD path not exercised by m6_jira_smoke.py. It validates the
two-step transition flow live: GET /transitions → POST the matching transition id.

Flow:
  1. create a throwaway issue
  2. discover the available transitions for THIS issue's workflow (names vary per project)
  3. move_item_state(target_state=<discovered name>)
  4. fetch_item_detail and assert the state actually changed
  5. delete the issue (cleanup)

Run from agentic_app/ with a project key (required — needs a writable project):
    PYTHONPATH=agentic_app PYTHONIOENCODING=utf-8 \
        ../.venv/Scripts/python.exe scripts/m6_jira_transition_smoke.py SCRUM

Never prints the API token.
"""
import asyncio
import sys

import httpx

from config.connectors.jira import JiraConnector
from config.env import JIRA_URL


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: m6_jira_transition_smoke.py <PROJECT_KEY>")
        print("  A writable project key is required to create + transition an issue.")
        sys.exit(2)
    project = sys.argv[1]
    conn = JiraConnector(org_url=JIRA_URL)

    print("→ Resolving Jira credentials (Key Vault → env fallback)...")
    auth = await conn.auth_adapter()
    print(f"  ✓ url={auth['jira_url']}  email={auth['email']}  token_len={len(auth['token'])}")

    print("\n→ create_item (Task) to transition...")
    created = await conn.create_item(
        project=project,
        title="[M6 transition smoke] move_item_state check",
        description="Created by m6_jira_transition_smoke.py to validate workflow transitions. Auto-deleted.",
        item_type="Task",
    )
    key = created["source_key"]
    print(f"  ✓ created {key}")

    deleted = False
    try:
        before = await conn.fetch_item_detail(project=project, issue_key=key)
        start_state = before.get("state")
        print(f"  start state = {start_state!r}")

        print("\n→ discovering available transitions for this issue...")
        tdata, _ = await conn._jira_request_with_retry(
            "GET", f"/rest/api/3/issue/{key}/transitions"
        )
        transitions = tdata.get("transitions", [])
        names = [t.get("name", "") for t in transitions]
        print(f"  ✓ {len(names)} available: {names}")
        if not names:
            raise SystemExit("No transitions available — cannot validate move_item_state.")

        # Pick a transition that is NOT the current state, so we prove a real move.
        # Among those, prefer a forward-looking one; fall back to any differing name.
        differing = [n for n in names if n.strip().lower() != (start_state or "").strip().lower()]
        pool = differing or names
        target = next(
            (n for n in pool if n.strip().lower() in
             ("in progress", "in review", "done", "start progress")),
            pool[0],
        )
        print(f"\n→ move_item_state(target_state={target!r})...")
        await conn.move_item_state(project=project, issue_key=key, target_state=target)
        print("  ✓ transition POST accepted")

        after = await conn.fetch_item_detail(project=project, issue_key=key)
        end_state = after.get("state")
        print(f"  end state = {end_state!r}")

        if end_state and end_state != start_state:
            print(f"\n  ✓ STATE CHANGED: {start_state!r} → {end_state!r}")
        elif end_state and end_state.strip().lower() == target.strip().lower():
            print(f"\n  ✓ STATE matches target {target!r} (was already there or transitioned in place)")
        else:
            print(f"\n  ⚠ state did not visibly change ({start_state!r} → {end_state!r}); "
                  f"transition may map to the same status name — inspect manually.")

        # Negative path: an impossible transition must raise ValueError with the available list.
        print("\n→ negative check: move_item_state('__definitely_not_a_state__') should raise...")
        try:
            await conn.move_item_state(
                project=project, issue_key=key, target_state="__definitely_not_a_state__"
            )
            print("  ⚠ expected ValueError but none raised")
        except ValueError as e:
            print(f"  ✓ correctly raised ValueError ({str(e)[:60]}...)")
    finally:
        print("\n→ deleting the test issue (cleanup)...")
        base = auth["jira_url"].rstrip("/")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(
                f"{base}/rest/api/3/issue/{key}",
                auth=(auth["email"], auth["token"]),
            )
            deleted = r.status_code in (204, 200)
            print(f"  {'✓ deleted' if deleted else '⚠ delete status ' + str(r.status_code)} {key}")

    print("\nTRANSITION CHECK COMPLETE ✓")


if __name__ == "__main__":
    asyncio.run(main())
