"""Milestone-6 inbound webhook — LOCAL round-trip smoke (no public tunnel).

Proves the parts of Step 2 that don't require provider-originated deliveries by
POSTing signed payloads directly at the locally-running FastAPI webhook router:

  SC-01  signature gate:   valid HMAC -> 200 {"status":"accepted"};
                           tampered HMAC -> 400 (generic Bad Request, no leak).
  SC-02  dedup gate:       100 identical deliveries (same delivery id) ->
                           exactly 1 "accepted", 99 "duplicate".

Reads the webhook secrets from config.env so they MATCH whatever the running
app loaded from .env (single source of truth — no cross-process drift).

Prereqs:
  - docker compose up -d redis (the router dedup uses app.state.redis_pool)
  - app running:  PYTHONPATH=. uvicorn process_api:app --port 8001
  - .env has GITHUB_WEBHOOK_SECRET, JIRA_WEBHOOK_SECRET, ENABLE_WEBHOOK_TRIGGERS=true

Run from agentic_app/:
  PYTHONPATH=. PYTHONIOENCODING=utf-8 ../.venv/Scripts/python.exe scripts/m6_webhook_local_smoke.py

Does NOT cover SC-06 (webhook -> run -> GET /runs): that path
has open code gaps (webhook-triggered runs are not persisted as Run rows and
`trigger` is not surfaced). Tracked separately.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid

import httpx

from config.env import (
    GITHUB_WEBHOOK_SECRET,
    JIRA_WEBHOOK_SECRET,
)

# Local target only — NOT config.env.AGENTIC_BASE_URL (that is the prod URL).
BASE = os.environ.get("SMOKE_BASE_URL", "http://localhost:8001").rstrip("/")
TENANT = os.environ.get("TEST_TENANT_ID") or "11111111-1111-1111-1111-111111111111"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()


async def sc01_github_signature_gate() -> bool:
    print("\n[SC-01] GitHub signature gate")
    if not GITHUB_WEBHOOK_SECRET:
        print("  ✗ GITHUB_WEBHOOK_SECRET not set in env — cannot test")
        return False

    body = json.dumps({
        "action": "opened",
        "issue": {"id": 1, "number": 7, "title": "M6 local smoke"},
        "repository": {"full_name": "test-org/test-repo"},
    }).encode()

    ok = True
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        # Valid signature -> 200 accepted
        good = await client.post(
            f"/webhooks/github/{TENANT}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(GITHUB_WEBHOOK_SECRET, body),
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-GitHub-Event": "issues",
            },
        )
        good_ok = good.status_code == 200 and good.json().get("status") == "accepted"
        print(f"  valid sig   -> {good.status_code} {good.json() if good.status_code==200 else good.text}  "
              f"{'✓' if good_ok else '✗'}")
        ok &= good_ok

        # Tampered signature -> 400 generic
        bad = await client.post(
            f"/webhooks/github/{TENANT}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeef",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-GitHub-Event": "issues",
            },
        )
        bad_ok = bad.status_code == 400
        print(f"  bad sig     -> {bad.status_code} {bad.text[:60]}  {'✓' if bad_ok else '✗'}")
        ok &= bad_ok
    return ok


async def sc02_dedup_stress(n: int = 100) -> bool:
    print(f"\n[SC-02] Dedup stress — {n} identical Jira deliveries")
    if not JIRA_WEBHOOK_SECRET:
        print("  ✗ JIRA_WEBHOOK_SECRET not set in env — cannot test")
        return False

    fixed_delivery_id = f"local-stress-{uuid.uuid4().hex[:8]}"
    body = json.dumps({
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "99999",
            "key": "STRESS-1",
            "fields": {
                "summary": "Dedup stress",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Story"},
            },
        },
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": _sign(JIRA_WEBHOOK_SECRET, body),
        "X-Atlassian-Webhook-Identifier": fixed_delivery_id,
    }

    accepted = duplicate = other = 0
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        for _ in range(n):
            r = await client.post(f"/webhooks/jira/{TENANT}", content=body, headers=headers)
            if r.status_code == 200:
                s = r.json().get("status")
                if s == "accepted":
                    accepted += 1
                elif s == "duplicate":
                    duplicate += 1
                else:
                    other += 1
            else:
                other += 1

    ok = accepted == 1 and duplicate == n - 1
    print(f"  accepted={accepted}  duplicate={duplicate}  other={other}  "
          f"(expected accepted=1 duplicate={n-1})  {'✓' if ok else '✗'}")
    return ok


async def sc06_roundtrip_visible_in_runs() -> bool:
    print("\n[SC-06] Webhook → consumer → Run row visible via GET /runs (trigger=webhook)")
    if not JIRA_WEBHOOK_SECRET:
        print("  ✗ JIRA_WEBHOOK_SECRET not set — cannot test")
        return False
    from config.auth.jwt import create_access_token

    event_id = f"roundtrip-{uuid.uuid4().hex}"
    body = json.dumps({
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "id": "10042",
            "key": "ROUND-1",
            "fields": {
                "summary": "Round-trip local smoke",
                "status": {"name": "In Progress"},
                "issuetype": {"name": "Task"},
            },
        },
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": _sign(JIRA_WEBHOOK_SECRET, body),
        "X-Atlassian-Webhook-Identifier": event_id,
    }

    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        post = await client.post(f"/webhooks/jira/{TENANT}", content=body, headers=headers)
        delivered = post.status_code == 200 and post.json().get("status") == "accepted"
        print(f"  delivery    -> {post.status_code} {post.json() if post.status_code==200 else post.text}  "
              f"{'✓' if delivered else '✗'}")
        if not delivered:
            return False

        token = create_access_token("m6-smoke-user", TENANT)
        expected_prefix = f"webhook-jira-{TENANT}"
        # Poll GET /runs — the consumer inserts the Run row asynchronously off the stream.
        found = None
        for attempt in range(10):
            await asyncio.sleep(1)
            runs = await client.get("/runs", headers={"Authorization": f"Bearer {token}"})
            if runs.status_code != 200:
                print(f"  GET /runs   -> {runs.status_code} {runs.text[:80]}")
                continue
            items = runs.json().get("items", [])
            found = next(
                (r for r in items
                 if r.get("trigger") == "webhook"
                 and expected_prefix in (r.get("id") or "")),
                None,
            )
            if found:
                print(f"  GET /runs   -> found after ~{attempt+1}s")
                break

    if found:
        print(f"  ✓ run id={found['id'][:8]} trigger={found['trigger']!r} "
              f"run={found['id']}")
        return True
    print("  ✗ no webhook-triggered run appeared in GET /runs")
    return False


async def main() -> None:
    print(f"Target: {BASE}   tenant={TENANT}")
    r1 = await sc01_github_signature_gate()
    r2 = await sc02_dedup_stress()
    r3 = await sc06_roundtrip_visible_in_runs()
    ok = r1 and r2 and r3
    print("\n" + ("ALL LOCAL WEBHOOK CHECKS PASSED ✓" if ok else "SOME CHECKS FAILED ✗"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
