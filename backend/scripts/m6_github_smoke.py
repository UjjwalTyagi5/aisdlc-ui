"""Milestone-6 GitHub Issues connector — outbound smoke test (local-dev).

Stage 1 (read-only): resolve installation token, list accessible repos, list issues.
Stage 2 (write):     create a test issue + comment in a chosen repo, then close it.

Run from agentic_app/:
    python scripts/m6_github_smoke.py            # read-only discovery
    python scripts/m6_github_smoke.py owner/repo # full read+write against one repo

Never prints the private key or token.
"""
import asyncio
import sys

import httpx

from config.connectors.github_issues import GitHubIssuesConnector, _GH_API_BASE


async def main() -> None:
    target_repo = sys.argv[1] if len(sys.argv) > 1 else ""

    conn = GitHubIssuesConnector()

    print("→ Resolving GitHub App installation token (JWT RS256 → access_tokens)...")
    token = await conn._get_installation_token()
    print(f"  ✓ token acquired (len={len(token)}, prefix={token[:4]}…)")

    headers = conn._gh_headers(token)
    async with httpx.AsyncClient(timeout=30) as client:
        rl = await client.get(f"{_GH_API_BASE}/rate_limit", headers=headers)
        rl.raise_for_status()
        core = rl.json()["resources"]["core"]
        print(f"  ✓ /rate_limit OK — {core['remaining']}/{core['limit']} core remaining")

        repos_resp = await client.get(
            f"{_GH_API_BASE}/installation/repositories",
            headers=headers,
            params={"per_page": 100},
        )
        repos_resp.raise_for_status()
        repos = repos_resp.json().get("repositories", [])
        print(f"\n→ Installation can access {len(repos)} repo(s):")
        for r in repos:
            print(f"    • {r['full_name']}  (issues={'on' if r.get('has_issues') else 'OFF'})")

    if not target_repo:
        print("\nRead-only discovery complete. Re-run with `owner/repo` to test write path.")
        return

    print(f"\n→ list_stories('{target_repo}', state=open)...")
    issues = await conn.list_stories(project=target_repo, state="open")
    print(f"  ✓ {len(issues)} open issue(s)")
    for i in issues[:5]:
        print(f"    #{i['source_key']}  {i['title']!r}  [{i['state']}]")

    print("\n→ create_item (test issue)...")
    created = await conn.create_item(
        project=target_repo,
        title="[M6 smoke test] connector outbound write check",
        body="Created by `m6_github_smoke.py` to validate the GitHub Issues connector. Safe to close.",
    )
    num = created["source_key"]
    print(f"  ✓ created issue #{num} — {created['url']}")

    print("\n→ add_comment...")
    await conn.add_comment(project=target_repo, issue_number=num, text="M6 smoke comment ✓")
    print("  ✓ comment posted")

    print("\n→ closing the test issue...")
    async with httpx.AsyncClient(timeout=30) as client:
        owner_repo = target_repo.strip("/")
        close = await client.patch(
            f"{_GH_API_BASE}/repos/{owner_repo}/issues/{num}",
            headers=headers,
            json={"state": "closed"},
        )
        close.raise_for_status()
    print(f"  ✓ issue #{num} closed")
    print("\nALL OUTBOUND CHECKS PASSED ✓")


if __name__ == "__main__":
    asyncio.run(main())
