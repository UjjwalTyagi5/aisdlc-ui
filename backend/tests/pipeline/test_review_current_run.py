"""Task 7 — Code Review current-run reads + standalone preservation.

Two structural guards:
  * the new current-run reader `_run_artifact_column` exists (pipeline mode reads
    the CURRENT run by id, not the project-latest row), and
  * the standalone reader `_latest_artifact_column` is NOT removed (dual-mode: the
    live-tested standalone Code Review page must keep working byte-for-byte).

Any DB-gated behavioural assertion is deferred to the Task 17 integration pass.
"""
from agents_orchestrator.code_review_agent.tools import review_tools


def test_has_current_run_reader():
    # The new helper reads a column off the CURRENT run, not project-latest.
    assert hasattr(review_tools, "_run_artifact_column")


def test_standalone_fallback_preserved():
    # The standalone helper must still exist and remain the fallback path.
    assert hasattr(review_tools, "_latest_artifact_column")
