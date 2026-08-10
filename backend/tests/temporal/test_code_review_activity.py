"""Tests for the code review Temporal activity registration (Task 4)."""
from __future__ import annotations

import pytest

from workflows.activity_dispatch import get_activity_fn, get_all_activity_fns


def test_code_review_activity_registered():
    fn = get_activity_fn("code_review")
    assert callable(fn)
    assert fn.__name__ == "run_code_review_activity"


def test_all_activities_includes_code_review():
    fns = get_all_activity_fns()
    names = {fn.__name__ for fn in fns}
    assert "run_code_review_activity" in names
