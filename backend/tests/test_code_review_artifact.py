"""CodeReviewArtifact model unit tests — Task 1 of Code Review Agent plan."""
from shared.models.artifacts import CodeReviewArtifact


def test_code_review_artifact_defaults():
    art = CodeReviewArtifact(pr_ref="https://github.com/org/repo/pull/42")
    assert art.pr_ref == "https://github.com/org/repo/pull/42"
    assert art.findings == []
    assert art.merge_recommendation is None
    assert art.review_summary is None
    assert art.semgrep_results is None
    assert art.version == 1


def test_code_review_artifact_with_findings():
    art = CodeReviewArtifact(
        pr_ref="PR#42",
        findings=[{
            "id": "F-001",
            "severity": "high",
            "category": "logic_error",
            "file": "src/auth.py",
            "line": 42,
            "description": "Missing null check",
            "recommendation": "Add guard clause",
        }],
        merge_recommendation="request_changes",
        review_summary="1 high finding, requires changes",
    )
    assert len(art.findings) == 1
    assert art.findings[0]["severity"] == "high"
    assert art.merge_recommendation == "request_changes"


def test_code_review_artifact_roundtrip():
    art = CodeReviewArtifact(pr_ref="PR#42", version=1)
    data = art.model_dump()
    restored = CodeReviewArtifact(**data)
    assert restored.pr_ref == art.pr_ref
    assert restored.version == art.version
