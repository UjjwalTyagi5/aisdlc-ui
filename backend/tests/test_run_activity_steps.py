"""Activity shows what a run produced, including work done through the chat.

THE PANEL SAID "NO ACTIVITY YET" NEXT TO A DOCUMENT THE USER HAD JUST DOWNLOADED.

Steps have no table; they are derived. The derivation read only the Run's JSONB stage
columns — requirements_payload, design_artifacts, development_artifacts,
testing_artifacts — which are the PIPELINE hand-off, how one agent passes structured
output to the next.

Chat-driven work never touches them. `register_generated_file` writes an `artifacts` row
and nothing else. So a run where somebody asked the Design agent for a PDF, approved it
and downloaded it derived NO steps at all: all four columns NULL, an artifact row
present, and an Activity panel reporting nothing had happened.

The artifacts are the evidence the work happened. Not reading them was the bug.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers._schemas import derive_steps_from_run  # noqa: E402

T0 = datetime(2026, 9, 2, 10, 49, tzinfo=timezone.utc)
RUN_ID = uuid.uuid4()


def _run(stage="design", **columns):
    return SimpleNamespace(
        id=RUN_ID,
        stage=stage,
        created_at=T0,
        updated_at=T0 + timedelta(minutes=5),
        requirements_payload=columns.get("requirements_payload"),
        design_artifacts=columns.get("design_artifacts"),
        development_artifacts=columns.get("development_artifacts"),
        testing_artifacts=columns.get("testing_artifacts"),
    )


def _artifact(*, filename="sdlc-password-reset-design.pdf", stored=True,
              size=28348, minutes=1, artifact_type="document"):
    path = f"{uuid.uuid4()}/{uuid.uuid4()}/{artifact_type}/{filename}" if filename else None
    return SimpleNamespace(
        id=uuid.uuid4(),
        artifact_type=artifact_type,
        blob_path=path,
        blob_url="https://acct.blob.core.windows.net/c/x" if stored else None,
        size_bytes=size,
        created_at=T0 + timedelta(minutes=minutes),
    )


# -- the bug ------------------------------------------------------------------


@pytest.mark.unit
def test_a_chat_produced_document_is_activity():
    """THE CASE FROM THE SCREENSHOT: every JSONB column NULL, one artifact row, and the
    panel previously showed nothing."""
    steps = derive_steps_from_run(_run(), [_artifact()])

    assert len(steps) == 1
    assert steps[0].kind == "artifact_write"
    assert steps[0].title == "sdlc-password-reset-design.pdf"
    assert "28,348 bytes" in steps[0].summary


@pytest.mark.unit
def test_a_run_with_no_columns_and_no_artifacts_is_still_empty():
    """Empty because nothing happened is correct; empty because nothing was read is not."""
    assert derive_steps_from_run(_run(), []) == []


@pytest.mark.unit
def test_omitting_artifacts_entirely_keeps_the_old_behaviour():
    """The parameter is optional — every existing caller passing only the run must keep
    deriving its stage steps."""
    steps = derive_steps_from_run(_run(design_artifacts={"hld": "x"}))
    assert len(steps) == 1
    assert steps[0].title == "Design stage completed"


# -- pipeline steps are not lost ----------------------------------------------


@pytest.mark.unit
def test_stage_steps_and_file_steps_both_appear():
    steps = derive_steps_from_run(
        _run(design_artifacts={"hld": "x"}), [_artifact()]
    )
    titles = [s.title for s in steps]
    assert "Design stage completed" in titles
    assert "sdlc-password-reset-design.pdf" in titles


@pytest.mark.unit
def test_steps_are_in_time_order_not_source_order():
    """Interleaved by when they happened, so the timeline reads as one sequence rather
    than two blocks."""
    steps = derive_steps_from_run(
        _run(design_artifacts={"hld": "x"}),
        [_artifact(filename="late.pdf", minutes=30), _artifact(filename="early.pdf", minutes=-30)],
    )
    assert [s.startedAt for s in steps] == sorted(s.startedAt for s in steps)
    assert steps[0].title == "early.pdf"


@pytest.mark.unit
def test_the_index_is_the_position_after_ordering():
    """It is what the timeline renders in; leaving the pre-sort value would number the
    rows out of sequence."""
    steps = derive_steps_from_run(
        _run(design_artifacts={"hld": "x"}),
        [_artifact(filename="a.pdf", minutes=2), _artifact(filename="b.pdf", minutes=9)],
    )
    assert [s.index for s in steps] == list(range(len(steps)))


# -- a failed upload must not read as success ---------------------------------


@pytest.mark.unit
def test_an_artifact_whose_upload_failed_is_not_reported_as_stored():
    """"Generated" reads as success. This is the one place a reader would otherwise
    never learn the bytes did not arrive."""
    steps = derive_steps_from_run(_run(), [_artifact(stored=False)])

    assert steps[0].status == "failed"
    assert "did not reach storage" in steps[0].summary


@pytest.mark.unit
def test_a_stored_artifact_is_approved():
    assert derive_steps_from_run(_run(), [_artifact()])[0].status == "approved"


# -- degenerate rows still describe a real event ------------------------------


@pytest.mark.unit
def test_an_artifact_with_no_path_is_named_by_its_type():
    """blob_path is NULL when blob storage was unconfigured. Something still happened."""
    steps = derive_steps_from_run(_run(), [_artifact(filename=None, stored=False)])
    assert len(steps) == 1
    assert steps[0].title == "document generated"


@pytest.mark.unit
def test_a_windows_path_leaf_is_extracted_too():
    """Legacy rows hold a local filesystem path, and on Windows it is backslash-separated."""
    art = _artifact()
    art.blob_path = r"C:\pwc_work\outputs\brd.docx"
    assert derive_steps_from_run(_run(), [art])[0].title == "brd.docx"


# -- ids stay usable as keys --------------------------------------------------


@pytest.mark.unit
def test_step_ids_are_deterministic_and_distinct():
    """A client keys on these across refetches; a collision would drop a row."""
    artifacts = [_artifact(filename="a.pdf"), _artifact(filename="b.pdf")]
    run = _run(design_artifacts={"hld": "x"})

    first = derive_steps_from_run(run, artifacts)
    second = derive_steps_from_run(run, artifacts)

    assert [s.id for s in first] == [s.id for s in second]
    assert len({s.id for s in first}) == len(first)


@pytest.mark.unit
def test_the_agent_comes_from_the_runs_stage():
    steps = derive_steps_from_run(_run(stage="requirements"), [_artifact()])
    assert steps[0].agent == "requirements"


@pytest.mark.unit
def test_an_unknown_stage_falls_back_to_a_valid_agent():
    """`agent` is a closed enum on the client — an unmapped stage passed straight
    through would fail schema validation and blank the whole panel."""
    steps = derive_steps_from_run(_run(stage="whatever"), [_artifact()])
    assert steps[0].agent == "orchestrator"
