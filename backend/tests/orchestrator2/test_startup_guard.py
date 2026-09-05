import inspect


def test_startup_calls_validate_registry():
    """A registry gap must stop the process, exactly as RBAC catalog drift does.
    Without this, a missing agent is discovered by a user getting no answer."""
    import process_api
    src = inspect.getsource(process_api)
    assert "validate_registry" in src, (
        "process_api must call validate_registry() at startup"
    )
    rbac_at = src.find("assert_rbac_catalog")
    reg_at = src.find("validate_registry")
    assert rbac_at != -1 and reg_at != -1
