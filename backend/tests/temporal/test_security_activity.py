import pytest

from workflows.activity_dispatch import get_activity_fn, get_all_activity_fns


def test_security_activity_registered():
    fn = get_activity_fn("security")
    assert callable(fn)
    assert fn.__name__ == "run_security_activity"


def test_all_activities_includes_security():
    fns = get_all_activity_fns()
    names = {fn.__name__ for fn in fns}
    assert "run_security_activity" in names
