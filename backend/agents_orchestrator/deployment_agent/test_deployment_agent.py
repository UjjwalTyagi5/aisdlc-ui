"""
Quick smoke test for the Deployment Agent REST endpoint.

Usage (from agentic_app/ with venv active):
    python agents_orchestrator/deployment_agent/test_deployment_agent.py

Requires the FastAPI server to be running:
    uvicorn process_api:app --reload --port 8001
"""

import asyncio
import json
import os
import sys
import zipfile
import io
import httpx

BASE_URL = "http://localhost:8001/sdlc/agent/deployment_orchestrator"
SESSION_ID = "test-session-001"
USER_ID = "test-user"

# ── Build minimal in-memory zips ──────────────────────────────────────────────

def _make_codebase_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", """\
USER_DB = {"alice": "s3cr3t", "bob": "p@ssw0rd"}

def login(username: str, password: str) -> dict:
    if not username or not password:
        return {"ok": False, "error": "Missing credentials"}
    if USER_DB.get(username) != password:
        return {"ok": False, "error": "Invalid credentials"}
    return {"ok": True, "token": "dummy-jwt"}

def health() -> dict:
    return {"status": "ok"}
""")
        zf.writestr("requirements.txt", "fastapi==0.116.0\nuvicorn==0.35.0\n")
    buf.seek(0)
    return buf.read()


def _make_testing_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test_report.md", """\
# Test Report

## Summary
- Total: 4
- Passed: 3
- Failed: 1

## Results
| ID | Name | Status |
|----|------|--------|
| T1 | login_valid_user | PASS |
| T2 | login_wrong_password | PASS |
| T3 | login_empty_username | PASS |
| T4 | login_sql_injection | FAIL |

## Notes
T4 failed: input sanitisation not implemented.
""")
    buf.seek(0)
    return buf.read()


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_sessions_info():
    print("\n[1] GET /sessions")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/sessions")
    print(f"  Status: {r.status_code}")
    data = r.json()
    assert "supported_files" in data, "Missing supported_files key"
    outputs = data['generated_outputs']
    print(f"  Outputs ({len(outputs)}): {outputs}")
    print("  PASS")


async def test_status_before_run():
    print("\n[2] GET /status/{session_id} (before run)")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/status/{SESSION_ID}", params={"user_id": USER_ID})
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Completion: {data['completion_percentage']}%")
    print("  PASS")


async def test_chat_dockerfile_only():
    print("\n[3] POST /chat/ — dockerfile only (codebase zip required)")
    codebase_bytes = _make_codebase_zip()

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{BASE_URL}/chat/",
            data={
                "session_id": SESSION_ID,
                "user_id": USER_ID,
                "user_message": "generate a dockerfile",
            },
            files={"uploaded_files": ("codebase.zip", codebase_bytes, "application/zip")},
        )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        responses = data.get("responses", "")
        print(f"  Response snippet: {responses[:200]}")
        has_handoff = False  # M2-06: sentinel mechanism removed
        print(f"  HANDOFF emitted: {has_handoff}")
        print("  PASS" if has_handoff else "  WARN: no HANDOFF found")
    else:
        print(f"  FAIL: {r.text[:300]}")


async def test_chat_full_pipeline():
    print("\n[4] POST /chat/ — full pipeline (codebase + testing zips)")
    codebase_bytes = _make_codebase_zip()
    testing_bytes = _make_testing_zip()

    async with httpx.AsyncClient(timeout=600) as client:
        r = await client.post(
            f"{BASE_URL}/chat/",
            data={
                "session_id": SESSION_ID + "-full",
                "user_id": USER_ID,
                "user_message": "create deployment files",
            },
            files=[
                ("uploaded_files", ("codebase.zip", codebase_bytes, "application/zip")),
                ("uploaded_files", ("test_reports.zip", testing_bytes, "application/zip")),
            ],
        )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        responses = data.get("responses", "")
        print(f"  Response snippet: {responses[:300]}")
        for artifact in (
            "Deployment plan",
            "Risk analysis",
            "Deployment validation",
            "Dockerfile",
            "Docker Compose",
            "Azure Pipelines",
            "GitHub Actions",
            "README",
            "Docker instructions",
            "Rollback plan",
        ):
            found = artifact.lower().replace(" ", "") in responses.lower().replace(" ", "")
            print(f"  {'✅' if found else '❌'} {artifact}")
        decision = data.get("deployment_decision", "")
        print(f"  Deployment decision: {decision or '(not in response JSON)'}")
        has_handoff = False  # M2-06: sentinel mechanism removed
        print(f"  HANDOFF emitted: {has_handoff}")
        print("  PASS" if has_handoff else "  WARN: no HANDOFF in response")
    else:
        print(f"  FAIL: {r.text[:300]}")


async def test_missing_files():
    print("\n[5] POST /chat/ — missing files guard")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/chat/",
            data={
                "session_id": SESSION_ID + "-nofiles",
                "user_id": USER_ID,
                "user_message": "create deployment files",
            },
        )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        resp = data.get("responses", "")
        has_missing_msg = "missing" in resp.lower() or "cannot proceed" in resp.lower()
        print(f"  Got missing-files message: {has_missing_msg}")
        print("  PASS" if has_missing_msg else f"  UNEXPECTED: {resp[:200]}")
    else:
        print(f"  FAIL: {r.text[:200]}")


async def test_ado_deploy():
    """ADO deploy mode — only runs if ADO_TEST_PROJECT and ADO_TEST_REPO are set."""
    project = os.environ.get("ADO_TEST_PROJECT")
    repo = os.environ.get("ADO_TEST_REPO")
    branch = os.environ.get("ADO_TEST_BRANCH", "main")
    if not (project and repo):
        print("\n[6] POST /chat/ — ADO deploy mode  (SKIPPED: set ADO_TEST_PROJECT and ADO_TEST_REPO to enable)")
        return

    print(f"\n[6] POST /chat/ — ADO deploy ({project}/{repo}@{branch})")
    deployment_request = json.dumps({
        "ado_project": project,
        "ado_repo": repo,
        "ado_branch": branch,
        "target_environment": "staging",
        "deployment_strategy": "rolling",
    })
    async with httpx.AsyncClient(timeout=600) as client:
        r = await client.post(
            f"{BASE_URL}/chat/",
            data={
                "session_id": SESSION_ID + "-ado",
                "user_id": USER_ID,
                "user_message": "deploy",
                "deployment_request": deployment_request,
            },
        )
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        run_url = data.get("pipeline_run_url", "")
        run_id = data.get("pipeline_run_id", "")
        print(f"  ado_project: {data.get('ado_project')}")
        print(f"  ado_repo: {data.get('ado_repo')}")
        print(f"  ado_branch: {data.get('ado_branch')}")
        print(f"  pipeline_run_id: {run_id}")
        print(f"  pipeline_run_url: {run_url}")
        responses = data.get("responses", "")
        print(f"  Response snippet: {responses[:300]}")
        print("  PASS" if run_url else "  WARN: no pipeline_run_url returned")
    else:
        print(f"  FAIL: {r.text[:300]}")


async def main():
    print("=" * 60)
    print("Deployment Agent Smoke Tests")
    print(f"Target: {BASE_URL}")
    print("=" * 60)

    # Quick connectivity check
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get("http://localhost:8001/health")
    except Exception:
        print("\nERROR: FastAPI server not reachable at http://localhost:8001")
        print("Start it first:")
        print("  cd agentic_app && uvicorn process_api:app --reload --port 8001")
        sys.exit(1)

    await test_sessions_info()
    await test_status_before_run()
    await test_missing_files()
    await test_chat_dockerfile_only()
    # Full pipeline — 5 Claude calls (~60–90s)
    await test_chat_full_pipeline()
    # ADO deploy mode (SKIPPED unless ADO_TEST_PROJECT + ADO_TEST_REPO set)
    await test_ado_deploy()

    print("\n" + "=" * 60)
    print("Done.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
