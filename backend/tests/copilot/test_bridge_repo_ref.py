import pytest

from agents_orchestrator.orchestrator import copilot_api


# ---------------------------------------------------------------------------
# _stage_needs_repo — truth table over all 8 pipeline stages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stage,expected",
    [
        ("requirements", False),
        ("design", False),
        ("development", True),
        ("code_review", True),
        ("security", True),
        ("testing", True),
        ("deployment", True),
        ("documentation", True),
    ],
)
def test_stage_needs_repo_truth_table(stage, expected):
    assert copilot_api._stage_needs_repo(stage) is expected


# ---------------------------------------------------------------------------
# _downstream_repo_ref — pure mapping from dev_artifacts -> repo_ref
# ---------------------------------------------------------------------------

def test_downstream_repo_ref_full_dict():
    dev_artifacts = {
        "repo_url": "https://dev.azure.com/org/proj/_git/repo",
        "branch_name": "feature/89-expense",
        "base_sha": "abc123",
        "target_branch": "main",
    }
    ref = copilot_api._downstream_repo_ref(dev_artifacts, "pat-value")
    assert ref == {
        "repo_url": "https://dev.azure.com/org/proj/_git/repo",
        "ref": "feature/89-expense",
        "base": "abc123",
        "pat": "pat-value",
    }


def test_downstream_repo_ref_base_falls_back_to_target_branch():
    dev_artifacts = {
        "repo_url": "https://dev.azure.com/org/proj/_git/repo",
        "branch_name": "feature/89-expense",
        "target_branch": "main",
    }
    ref = copilot_api._downstream_repo_ref(dev_artifacts, "pat-value")
    assert ref["base"] == "main"


def test_downstream_repo_ref_base_defaults_to_main_when_absent():
    """C3 — neither base_sha nor target_branch is populated today, so without a
    default `base` is None and `prepare_run_workspace` never computes a diff.
    Falls back to "main" (prepare_run_workspace diffs origin/{base}...HEAD, and
    origin/main exists on any repo cloned from the default remote)."""
    dev_artifacts = {
        "repo_url": "https://dev.azure.com/org/proj/_git/repo",
        "branch_name": "feature/89-expense",
    }
    ref = copilot_api._downstream_repo_ref(dev_artifacts, "pat-value")
    assert ref["base"] == "main"


def test_downstream_repo_ref_empty_dict_returns_none():
    assert copilot_api._downstream_repo_ref({}, "pat-value") is None


def test_downstream_repo_ref_none_returns_none():
    assert copilot_api._downstream_repo_ref(None, "pat-value") is None


def test_downstream_repo_ref_missing_repo_url_returns_none():
    dev_artifacts = {"branch_name": "feature/89-expense"}
    assert copilot_api._downstream_repo_ref(dev_artifacts, "pat-value") is None


def test_downstream_repo_ref_pat_optional():
    dev_artifacts = {"repo_url": "https://example.com/repo", "branch_name": "b"}
    ref = copilot_api._downstream_repo_ref(dev_artifacts, None)
    assert ref["pat"] is None
