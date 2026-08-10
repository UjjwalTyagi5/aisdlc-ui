"""Task 3 — `sections_from_run` emits a `file-tree` artifact section per downstream
stage that captured generated files (Task 2 sets `has_files: True` on the persisted
`{stage}_artifacts` dict), so a reloaded/replayed run shows the same file-tree the
live path already streams via `artifact.ready`."""
from types import SimpleNamespace

from shared.services.orchestrator.artifacts_view import (
    sections_from_run,
    stage_files_section,
)


def _run(**cols):
    """Fake run object exposing only the attributes sections_from_run reads."""
    base = {
        "requirements_payload": None,
        "design_artifacts": None,
        "development_artifacts": None,
        "code_review_artifacts": None,
        "security_artifacts": None,
        "testing_artifacts": None,
        "deployment_artifacts": None,
        "documentation_artifacts": None,
    }
    base.update(cols)
    return SimpleNamespace(**base)


def test_stage_files_section_shape():
    sec = stage_files_section("testing")
    assert sec == {
        "id": "testing-files",
        "stage": "testing",
        "kind": "file-tree",
        "title": "Generated files",
        "source": "testing",
    }


def test_has_files_true_emits_file_tree_alongside_report():
    run = _run(testing_artifacts={
        "sections": [{
            "id": "testing-report", "stage": "testing", "kind": "markdown",
            "title": "Testing Report", "content": "# Testing",
        }],
        "has_files": True,
    })
    out = sections_from_run(run)
    kinds = [(s["stage"], s["kind"]) for s in out]
    assert ("testing", "markdown") in kinds
    assert ("testing", "file-tree") in kinds
    file_tree = next(s for s in out if s["kind"] == "file-tree")
    assert file_tree["source"] == "testing"
    assert file_tree["id"] == "testing-files"


def test_no_has_files_flag_omits_file_tree():
    run = _run(security_artifacts={
        "sections": [{
            "id": "security-report", "stage": "security", "kind": "markdown",
            "title": "Security Report", "content": "# Security",
        }],
    })
    out = sections_from_run(run)
    kinds = [(s["stage"], s["kind"]) for s in out]
    assert ("security", "markdown") in kinds
    assert not any(k == ("security", "file-tree") for k in kinds)


def test_has_files_false_omits_file_tree():
    run = _run(deployment_artifacts={
        "sections": [{
            "id": "deployment-report", "stage": "deployment", "kind": "markdown",
            "title": "Deployment Report", "content": "# Deployment",
        }],
        "has_files": False,
    })
    out = sections_from_run(run)
    assert not any(s["kind"] == "file-tree" for s in out)
