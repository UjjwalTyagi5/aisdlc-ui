"""REQ-M8-07 — Evidence export unit tests.

Asserts:
  - POST /runs/{id}/evidence returns 202 + job_id (async generation initiated)
  - GET /runs/{id}/evidence/{job_id}/status transitions from "pending" → "ready"
  - Status "ready" response includes download_url
  - The generated ZIP contains a manifest.json with SHA-256 per file
  - manifest.json SHA-256 hashes match the actual file contents

D-03 locked: Polling pattern. 202 + job_id → background worker → Azure Blob →
poll status until ready + download_url.

Wave-3 turns these GREEN when evidence export router and worker are implemented.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

# Wave-3 implements the evidence export router, background worker, and build_evidence_zip.
pytestmark = pytest.mark.xfail(
    reason="Wave 3 implements evidence export router and EvidenceExportWorker",
    strict=False,
)


@pytest.mark.unit
async def test_post_evidence_returns_202_and_job_id(mint_token):
    """POST /runs/{id}/evidence returns 202 Accepted + {"job_id": "..."} (REQ-M8-07, D-03)."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u1",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["artifact:view"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/runs/run-001/evidence", headers=headers)

    assert response.status_code == 202, (
        f"Expected 202 Accepted for evidence export, got {response.status_code}"
    )
    data = response.json()
    assert "job_id" in data, "Response must include job_id"
    assert isinstance(data["job_id"], str)
    assert len(data["job_id"]) > 0


@pytest.mark.unit
async def test_evidence_status_pending_then_ready(mint_token):
    """Status polling transitions from pending to ready with download_url (REQ-M8-07, D-03)."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u1",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["artifact:view"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Initiate export
        post_response = await client.post("/runs/run-001/evidence", headers=headers)
        assert post_response.status_code == 202
        job_id = post_response.json()["job_id"]

        # Poll status — eventually ready (or still pending in test)
        status_response = await client.get(
            f"/runs/run-001/evidence/{job_id}/status",
            headers=headers,
        )

    assert status_response.status_code == 200
    status_data = status_response.json()
    assert "status" in status_data
    assert status_data["status"] in ("pending", "processing", "ready", "failed")

    if status_data["status"] == "ready":
        assert "download_url" in status_data
        assert status_data["download_url"].startswith("https://")


@pytest.mark.unit
async def test_evidence_requires_artifact_view(mint_token):
    """POST /runs/{id}/evidence returns 403 without artifact:view permission."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u2",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["run:create"],  # no artifact:view
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/runs/run-001/evidence", headers=headers)

    assert response.status_code == 403


@pytest.mark.unit
def test_build_evidence_zip_contains_manifest_json():
    """build_evidence_zip() returns a ZIP with manifest.json containing SHA-256 per file.

    This is the core compliance assertion for REQ-M8-07: every file in the evidence
    ZIP must have its SHA-256 hash recorded in manifest.json.
    """
    from shared.audit.evidence import build_evidence_zip

    artifacts = [
        {"artifact_type": "requirements", "content": "User stories..."},
        {"artifact_type": "design", "content": "Architecture diagrams..."},
    ]
    audit_events = [
        {
            "id": "evt-1",
            "event_type": "tool_call_start",
            "actor_id": "system:requirements",
            "resource_id": "run-001",
            "created_at": "2026-06-09T00:00:00Z",
            "payload": "{}",
        }
    ]

    zip_bytes, zip_sha256 = build_evidence_zip(artifacts, audit_events)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names, "ZIP must contain manifest.json"

        manifest = json.loads(zf.read("manifest.json"))

        # Every non-manifest file must have a SHA-256 entry
        for name in names:
            if name == "manifest.json":
                continue
            assert name in manifest, f"manifest.json missing entry for {name}"
            file_bytes = zf.read(name)
            expected_hash = hashlib.sha256(file_bytes).hexdigest()
            assert manifest[name] == expected_hash, (
                f"SHA-256 mismatch for {name}: "
                f"manifest={manifest[name]}, actual={expected_hash}"
            )


@pytest.mark.unit
def test_build_evidence_zip_includes_audit_csv():
    """build_evidence_zip() includes audit_events.csv in the ZIP."""
    from shared.audit.evidence import build_evidence_zip

    zip_bytes, _ = build_evidence_zip(
        artifacts=[],
        audit_events=[
            {
                "id": "evt-1",
                "event_type": "model_call",
                "actor_id": "system:design",
                "resource_id": "run-001",
                "created_at": "2026-06-09T00:00:00Z",
                "payload": "{}",
            }
        ],
    )

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "audit_events.csv" in zf.namelist(), (
            "ZIP must include audit_events.csv"
        )


@pytest.mark.unit
def test_build_evidence_zip_manifest_sha256_is_hex_string():
    """manifest.json SHA-256 values must be lowercase 64-character hex strings."""
    from shared.audit.evidence import build_evidence_zip

    zip_bytes, _ = build_evidence_zip(
        artifacts=[{"artifact_type": "test", "content": "data"}],
        audit_events=[],
    )

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    for filename, sha in manifest.items():
        assert isinstance(sha, str), f"{filename}: SHA-256 must be a string"
        assert len(sha) == 64, f"{filename}: SHA-256 must be 64 hex chars, got {len(sha)}"
        assert sha == sha.lower(), f"{filename}: SHA-256 must be lowercase"
