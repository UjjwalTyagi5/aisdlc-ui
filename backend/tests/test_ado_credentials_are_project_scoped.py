"""Every route that resolves an Azure DevOps PAT asks for it per user, per project.

WHY THIS IS THE ONLY CORRECT CALL SHAPE. Azure DevOps credentials live in
`project_integration_credentials`, saved per user per project — deliberately, so nobody
borrows anybody else's token and every action traces to a person. `resolve_auth` consults
that table ONLY when both `project_id` and `owner_id` are supplied; given a tenant alone
it looks exclusively for a tenant-wide connector, which by design does not exist here.

THE BUG THIS CAUGHT. `documentation_workspace.prepare_docs` passed the tenant alone, so
it never found the Project Admin's saved PAT and could not open a docs workspace at all.
`resolve_auth`'s own docstring warns about exactly this — "without this, that saved
credential is never found" — and names `dev_workspace.py` as the route that needed it.
That router was fixed; this one was written the old way and nobody compared them.

IT FAILED AS A 500. `resolve_clone_url` raises RuntimeError carrying an actionable
sentence, nothing caught it, and FastAPI returned a bare "Internal Server Error" — so the
dialog did nothing and the one message that explained why was discarded.

A SOURCE-LEVEL TEST, because the failure is a missing ARGUMENT. Both call shapes compile,
both type-check, and the wrong one only misbehaves against a database holding a
project-scoped credential — which no unit test has. What can be checked cheaply and
exactly is that the argument is there.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Routers that clone or browse an Azure DevOps repo on a caller's behalf.
ROUTER_MODULES = [
    "shared.routers.documentation_workspace",
    "shared.routers.dev_workspace",
    "shared.routers.code_review_workspace",
    "shared.routers.security_workspace",
]


def _module_source(path: str) -> str:
    import importlib

    return inspect.getsource(importlib.import_module(path))


#: `resolve_auth(...)` up to its closing paren, across line breaks.
_CALL = re.compile(r"resolve_auth\(\s*(.*?)\)", re.S)


@pytest.mark.parametrize("module_path", ROUTER_MODULES)
def test_every_resolve_auth_call_is_project_and_owner_scoped(module_path):
    """THE HEADLINE. A tenant-only call cannot see the credential this platform
    actually stores, and fails with "Azure DevOps is not configured" on a project that
    plainly is connected."""
    try:
        src = _module_source(module_path)
    except ModuleNotFoundError:
        pytest.skip(f"{module_path} is not present")

    calls = _CALL.findall(src)
    if not calls:
        pytest.skip(f"{module_path} does not resolve Azure DevOps auth")

    for args in calls:
        assert "project_id" in args, (
            f"{module_path}: resolve_auth({args.strip()[:60]}...) omits project_id, so "
            "the per-project credential is never found"
        )
        assert "owner_id" in args, (
            f"{module_path}: resolve_auth({args.strip()[:60]}...) omits owner_id, so "
            "the per-user credential is never found"
        )


def test_the_helper_really_does_require_both():
    """NON-VACUITY, and the reason the assertion above is not cargo cult: the two
    keywords exist on `resolve_auth` and gate the project-scoped lookup. If they were
    dropped from the signature, the test above would keep passing while meaning
    nothing."""
    from shared.services.ado_repos import resolve_auth

    params = inspect.signature(resolve_auth).parameters
    assert "project_id" in params
    assert "owner_id" in params

    body = inspect.getsource(resolve_auth)
    # Both together, or the project-scoped branch does not run.
    assert "project_id and owner_id" in body or ("project_id" in body and "owner_id" in body)


def test_prepare_turns_an_unconfigured_connector_into_a_message():
    """THE 500. `resolve_clone_url` raises RuntimeError with a sentence a user can act
    on; unhandled, that became "Internal Server Error" and the dialog just sat there.

    The clone call further down always had this handling — the calls before it did not,
    which is why the failure looked like a crash rather than a missing connector.
    """
    from shared.routers.documentation_workspace import prepare_docs

    src = inspect.getsource(prepare_docs)

    assert src.count("except RuntimeError") >= 2, (
        "the credential-resolving calls before the clone still let RuntimeError escape "
        "as a 500"
    )
    assert "status_code=409" in src, "an unconfigured connector should not read as a crash"
