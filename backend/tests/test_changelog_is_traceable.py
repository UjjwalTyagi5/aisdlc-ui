"""The changelog names the work each commit delivers, or admits that it cannot.

WHAT IT USED TO BE. "conventional, grouped from git history (use generate_changelog)" —
one tool call, `git log --pretty=%s`, bucketed by conventional-commit prefix. Accurate,
and almost useless to a reader: on a real branch it produced two bullets under "Added",
one under "Changed", and a closing note that no further commit detail was present. The
project's approved requirements sat one tool call away and were never opened, so a
changelog for three epics could not say which epic any line belonged to.

WHY THIS IS A PROMPT TEST. The mapping is a judgement the model makes across three tool
results; there is no function to call and assert on. What CAN be pinned is the contract
the model is given — that it reads all three sources, that git remains the only source
of entries, and that an unmappable commit survives unchanged rather than being dropped
or given an invented story. Those are the three ways this feature turns into fiction,
and each is asserted here by the sentence that forbids it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.documentation_agent.prompts.doc_prompt import (  # noqa: E402
    DOC_SYSTEM_PROMPT,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def changelog_section() -> str:
    """Just the changelog deliverable — the next bullet starts the release notes."""
    start = DOC_SYSTEM_PROMPT.index("- **changelog**")
    end = DOC_SYSTEM_PROMPT.index("- **release_notes**", start)
    return DOC_SYSTEM_PROMPT[start:end]


def test_it_reads_the_requirements_as_well_as_the_git_history(changelog_section):
    for tool in ("generate_changelog", "read_upstream_artifacts",
                 "list_project_documents", "read_document"):
        assert tool in changelog_section, f"changelog no longer reads {tool}"


def test_git_stays_the_only_source_of_entries(changelog_section):
    """The requirements say what was ASKED for; only the commits say what was DONE.
    An entry sourced from a story would document work that may not exist."""
    assert re.search(r"must come from one of these commits", changelog_section)


def test_an_unmappable_commit_is_kept_verbatim(changelog_section):
    """The three failure modes, each forbidden by name. Dropping a commit hides real
    work; inventing a story misattributes it; renumbering one corrupts the id that
    made the mapping checkable in the first place."""
    lowered = changelog_section.lower()
    assert "unchanged" in lowered
    assert "never invent" in lowered
    assert "never drop it" in lowered
    assert "never renumber" in lowered


def test_a_changelog_without_requirements_says_so(changelog_section):
    """Silence here reads as "the mapping was done and found nothing", which is the
    opposite of the truth when no approved requirements existed to map against."""
    assert "git history alone" in changelog_section
    assert "no approved requirements were available" in changelog_section


def test_the_button_asks_for_the_same_thing_as_the_prompt():
    """The quick action sends its own sentence as the user's turn. Left as "generate a
    grouped changelog from the git history", it contradicted the deliverable spec — and
    the nearer instruction is the one a model follows."""
    page = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "app" / "(app)" / "projects" / "[id]" / "documentation" / "page.tsx"
    )
    if not page.exists():  # backend-only checkout
        pytest.skip("frontend not present in this checkout")
    line = next(
        ln for ln in page.read_text(encoding="utf-8").splitlines()
        if '{ key: "changelog"' in ln
    )
    assert "epic or user story" in line
    assert "cannot tie" in line
