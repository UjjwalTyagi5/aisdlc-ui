"""Evidence ZIP builder (REQ-M8-07).

Builds an in-memory ZIP containing:
  artifacts/{artifact_type}.json  — one file per artifact type
  audit_events.csv                — all audit events for the run
  manifest.json                   — SHA-256 (hex) per file, matching actual content

The ZIP bytes + overall ZIP SHA-256 digest are returned as a tuple.
This module is intentionally standalone (no FastAPI / SQLAlchemy imports) so
it is unit-testable without the full app context.

Threat mitigations:
  T-M8-16 — manifest.json SHA-256 per file matches content; read-only SAS prevents overwrite.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from typing import Any


def build_evidence_zip(
    artifacts: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Build an evidence ZIP in memory.

    Args:
        artifacts:    List of artifact dicts, each with at least an
                      ``artifact_type`` key.  Other keys are serialised as JSON.
        audit_events: List of audit event dicts to serialise as CSV rows.

    Returns:
        (zip_bytes, zip_sha256_hex) — the raw ZIP bytes and the SHA-256 of the
        entire ZIP file (suitable for a manifest of the whole export, not to be
        confused with the per-file manifest.json inside the ZIP).
    """
    buf = io.BytesIO()

    # Track (filename -> raw bytes) so the manifest hashes match what was written.
    file_contents: dict[str, bytes] = {}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # ── artifacts/{type}.json ────────────────────────────────────────────
        # Group artifacts by type; multiple artifacts of the same type are
        # serialised as a JSON array under artifacts/{type}.json.
        artifacts_by_type: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            atype = str(artifact.get("artifact_type", "unknown"))
            artifacts_by_type.setdefault(atype, []).append(artifact)

        for atype, items in artifacts_by_type.items():
            fname = f"artifacts/{atype}.json"
            content = json.dumps(items, default=str, indent=2).encode()
            file_contents[fname] = content

        # ── audit_events.csv ─────────────────────────────────────────────────
        if audit_events:
            csv_buf = io.StringIO()
            # Derive header from the union of all keys across all events.
            fieldnames: list[str] = []
            seen: set[str] = set()
            for evt in audit_events:
                for k in evt:
                    if k not in seen:
                        fieldnames.append(k)
                        seen.add(k)
            writer = csv.DictWriter(csv_buf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(audit_events)
            csv_bytes = csv_buf.getvalue().encode()
        else:
            # Always include the CSV even when empty (header-only)
            csv_bytes = b"id,event_type,actor_id,resource_id,created_at,payload\r\n"
        file_contents["audit_events.csv"] = csv_bytes

        # ── Write all non-manifest files first ───────────────────────────────
        for fname, content in file_contents.items():
            zf.writestr(fname, content)

        # ── manifest.json — SHA-256 per file ─────────────────────────────────
        # Computed from the in-memory bytes, not re-read from the ZIP, so hashes
        # are guaranteed to match the written content (T-M8-16).
        manifest: dict[str, str] = {
            fname: hashlib.sha256(content).hexdigest()
            for fname, content in file_contents.items()
        }
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
        zf.writestr("manifest.json", manifest_bytes)

    zip_bytes = buf.getvalue()
    zip_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    return zip_bytes, zip_sha256
