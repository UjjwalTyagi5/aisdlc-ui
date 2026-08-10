"""Milestone-6 Jira connector — outbound smoke test (local-dev).

Stage 1 (read-only): resolve creds, list projects, list issues in first project.
Stage 2 (write):     create a test issue + comment, then delete it (cleanup).

Run from agentic_app/:
    python scripts/m6_jira_smoke.py            # read-only discovery
    python scripts/m6_jira_smoke.py PROJKEY    # full read+write in one project

Never prints the API token.
"""
import asyncio
import sys

import httpx

from config.connectors.jira import JiraConnector
from config.env import JIRA_URL


async def main() -> None:
    target_project = sys.argv[1] if len(sys.argv) > 1 else ""
    conn = JiraConnector(org_url=JIRA_URL)

    print("→ Resolving Jira credentials (Key Vault → env fallback)...")
    auth = await conn.auth_adapter()
    print(f"  ✓ url={auth['jira_url']}  email={auth['email']}  token_len={len(auth['token'])}")

    print("\n→ list_projects()...")
    projects = await conn.list_projects()
    print(f"  ✓ {len(projects)} project(s):")
    for p in projects[:10]:
        print(f"    • {p['source_key']}  {p['title']!r}")

    if not target_project:
        if projects:
            print(f"\nRe-run with a project key (e.g. {projects[0]['source_key']}) to test the write path.")
        else:
            print("\nNo projects visible — create a project in Jira first, then re-run with its key.")
        return

    print(f"\n→ list_stories('{target_project}')...")
    issues = await conn.list_stories(project=target_project)
    print(f"  ✓ {len(issues)} issue(s)")
    for i in issues[:5]:
        print(f"    {i['source_key']}  {i['title']!r}  [{i['state']}]")

    print("\n→ create_item (Task)...")
    created = await conn.create_item(
        project=target_project,
        title="[M6 smoke test] Jira connector outbound write check",
        description="Created by m6_jira_smoke.py to validate the Jira connector. Auto-deleted.",
        item_type="Task",
    )
    key = created["source_key"]
    print(f"  ✓ created {key}")

    print("\n→ add_comment...")
    await conn.add_comment(project=target_project, issue_key=key, text="M6 smoke comment OK")
    print("  ✓ comment posted")

    print("\n→ fetch_item_detail...")
    detail = await conn.fetch_item_detail(project=target_project, issue_key=key)
    print(f"  ✓ {detail['source_key']}  state={detail.get('state')!r}  type={detail.get('item_type') or detail.get('work_item_type')!r}")

    print("\n→ deleting the test issue (cleanup)...")
    base = auth["jira_url"].rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.delete(
            f"{base}/rest/api/3/issue/{key}",
            auth=(auth["email"], auth["token"]),
        )
        print(f"  {'✓ deleted' if r.status_code in (204, 200) else '⚠ delete status ' + str(r.status_code)} {key}")

    print("\nALL OUTBOUND CHECKS PASSED ✓")


if __name__ == "__main__":
    asyncio.run(main())
