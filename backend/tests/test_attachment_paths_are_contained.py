"""An attachment path from the browser can only name an attachment.

WHAT THIS CLOSES. `pipeline_context.attachments` is client-supplied JSON. The upload
route decides where a file is WRITTEN; until now nothing decided what could be named
for READING. A crafted chat turn —

    {"attachments": [{"path": "C:/pwc_work/frontend/backend/.env"}]}

— had every agent that reads attachments extract that file and hand its text to the
model, which then quotes it back in the conversation. No permission stood in the way:
opening a chat on any project you can see was enough.

The check lives in `attachment_paths_from_context`, the single place that shape is
decoded, so it cannot be forgotten at one of the nine agents that call it. Paths the
SERVER derived (an agent's own input directory) never pass through that function and
are unaffected.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.services.attachment_store import _FILES_ROOT  # noqa: E402
from shared.tools.document_tools import attachment_paths_from_context  # noqa: E402

pytestmark = pytest.mark.unit

LEGIT = str(_FILES_ROOT / "u1" / "attachments" / "s1" / "brd.docx")


def _paths(*raw: str) -> list[str]:
    return attachment_paths_from_context(
        {"attachments": [{"path": p} for p in raw]}
    )


def test_a_real_attachment_still_gets_through():
    """NON-VACUITY FIRST. A containment check that rejected everything would pass every
    test below and break the feature completely."""
    assert _paths(LEGIT) == [LEGIT]


@pytest.mark.parametrize(
    "hostile, what",
    [
        (str(ROOT / ".env"), "a secrets file next to the app"),
        ("C:/Windows/win.ini", "an absolute path outside the tree"),
        ("/etc/passwd", "the posix equivalent"),
        (str(_FILES_ROOT / "u1" / "attachments" / ".." / ".." / ".." / ".env"),
         "traversal back out of the attachment store"),
        (str(_FILES_ROOT / "u1" / "requirements_agent" / "s1" / "input" / "x.docx"),
         "a sibling directory that is not attachments"),
        (str(_FILES_ROOT / "u1" / "attachments"),
         "the directory itself rather than a file in a session"),
    ],
)
def test_nothing_outside_the_attachment_store_is_readable(hostile: str, what: str):
    assert _paths(hostile) == [], f"reachable: {what}"


def test_one_hostile_path_does_not_take_the_good_ones_with_it():
    """The legitimate attachments in the same turn still reach the agent — dropping the
    lot would turn a rejected path into a silent failure of the whole feature."""
    assert _paths(LEGIT, "C:/Windows/win.ini") == [LEGIT]


def test_shapes_that_are_not_attachments_at_all():
    """The decoder is fed whatever the client sent, including nothing."""
    assert attachment_paths_from_context(None) == []
    assert attachment_paths_from_context("not a dict") == []
    assert attachment_paths_from_context({}) == []
    assert attachment_paths_from_context({"attachments": None}) == []
    assert attachment_paths_from_context({"attachments": [{"no_path": 1}, "x", None]}) == []
