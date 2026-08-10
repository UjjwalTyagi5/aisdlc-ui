"""`submit_code_review` must succeed on a realistic multi-finding payload.

Root cause of the live bug: the Copilot pipeline's `_seed_downstream_prepared`
(`agents_orchestrator/orchestrator/copilot_api.py`) seeds `s.changed_files` as the
raw `list[str]` from `RunWorkspace.changed_files`, while the Temporal activity path
(`workflows/activities/code_review_activity.py`) converts it to `list[dict]` first.
`submit_code_review`'s metrics block called `f.get(...)` on every entry unconditionally,
so under the Copilot-seeded shape it raised `AttributeError: 'str' object has no
attribute 'get'` BEFORE the try/except around artifact construction even ran —
failing "regardless of payload" exactly as reported live. Separately, `ReviewFinding`
`/`CoverageEntry`/`ConformanceEntry` used closed `Literal` enums with no coercion, so
one out-of-set severity/category value would `ValidationError` the whole 9-finding
batch instead of just that finding.
"""
import json

import pytest

from agents_orchestrator.code_review_agent.tools.review_tools import submit_code_review
from agents_orchestrator.code_review_agent.config.session_state import get_session
from config.ws_helper import set_session_id, reset_session_id


def _nine_findings() -> list[dict]:
    findings = []
    for i in range(1, 8):
        findings.append({
            "id": f"F-{i:03d}",
            "severity": "high" if i % 2 == 0 else "medium",
            "category": "logic_error",
            "file": f"src/File{i}.cs",
            "line": 10 + i,
            "description": f"Issue number {i}",
            "recommendation": f"Fix issue {i}",
            "autofix_patch": f"--- a/src/File{i}.cs\n+++ b/src/File{i}.cs\n",
        })
    # F-008: severity/category values outside the closed enum set (mis-cased + invented).
    findings.append({
        "id": "F-008",
        "severity": "BLOCKER",
        "category": "db_issue",
        "file": "src/File8.cs",
        "line": 42,
        "description": "Unrecognized enum values must be normalized, not rejected.",
    })
    # F-009: missing optional fields (recommendation, autofix_patch, even file/line).
    findings.append({
        "id": "F-009",
        "severity": "low",
        "category": "style",
        "description": "Missing optional fields must not fail construction.",
    })
    assert len(findings) == 9
    return findings


@pytest.fixture
def review_payload() -> dict:
    return {
        "summary": "## PR Review\nNine findings identified across four files.",
        "merge_recommendation": "request_changes",
        "findings": _nine_findings(),
        "requirements_coverage": [
            {"ac_id": "AC-1", "status": "satisfied", "note": "Covered"},
            {"ac_id": "AC-2", "status": "weird_status_value", "note": "Should fall back"},
        ],
        "design_conformance": [
            {"rule": "API contract", "status": "conforms", "note": "OK"},
            {"rule": "Schema boundary", "status": "TBD", "note": "Should fall back"},
        ],
        "metrics": {"complexity_delta": 1.5, "dupe_delta": 0.0, "debt_delta": -0.5},
    }


@pytest.fixture
def review_session():
    """A session whose `changed_files` mirrors the Copilot pipeline's (buggy) shape:
    a plain `list[str]`, not the `list[dict]` the Temporal activity path produces."""
    token = set_session_id("test-submit-code-review-session")
    s = get_session("test-submit-code-review-session")
    s.changed_files = ["src/File1.cs", "src/File2.cs", "src/File3.cs"]
    s.diff_text = "diff --git a/src/File1.cs b/src/File1.cs\n"
    s.repo_name = "TestRepo"
    s.mode = "branch"
    s.source_branch = "feature/x"
    s.base_branch = "main"
    yield s
    reset_session_id(token)
    s.last_artifact = None
    s.changed_files = []


@pytest.mark.asyncio
async def test_submit_code_review_succeeds_with_changed_files_as_strings(review_payload, review_session):
    """Reproduces the live bug: Copilot-seeded `changed_files: list[str]` must not
    crash the tool with AttributeError, regardless of the review payload content."""
    result = await submit_code_review.ainvoke({"review_json": json.dumps(review_payload)})

    assert "error" not in result.lower(), f"submit_code_review reported an error: {result}"
    assert "submitted" in result.lower()


@pytest.mark.asyncio
async def test_submit_code_review_builds_valid_artifact_and_sets_last_artifact(review_payload, review_session):
    await submit_code_review.ainvoke({"review_json": json.dumps(review_payload)})

    assert review_session.last_artifact is not None
    artifact = review_session.last_artifact

    # Programmatic re-validation of the stored artifact (not just string-parsing the reply).
    from shared.models.code_review import CodeReviewArtifact
    rebuilt = CodeReviewArtifact(**artifact)

    assert len(rebuilt.findings) == 9
    assert rebuilt.merge_recommendation == "request_changes"

    f8 = next(f for f in rebuilt.findings if f.id == "F-008")
    assert f8.severity in ("critical", "high", "medium", "low", "info")
    assert f8.category in (
        "logic_error", "security", "performance", "style", "design", "maintainability", "other",
    )

    f9 = next(f for f in rebuilt.findings if f.id == "F-009")
    assert f9.file == ""
    assert f9.line == 0
    assert f9.autofix_patch is None

    coverage_by_ac = {c.ac_id: c for c in rebuilt.requirements_coverage}
    assert coverage_by_ac["AC-2"].status in ("satisfied", "violated", "unimplemented", "partial")

    conformance_by_rule = {c.rule: c for c in rebuilt.design_conformance}
    assert conformance_by_rule["Schema boundary"].status in ("conforms", "drifts", "violates", "unknown")

    # The metrics block is where the live AttributeError actually fired.
    assert rebuilt.metrics.files_changed == 3
