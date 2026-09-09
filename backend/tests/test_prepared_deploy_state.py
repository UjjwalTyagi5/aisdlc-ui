"""The prepared deployment target survives a reload, and never carries the credential.

TWO THINGS, and the second is the one that would hurt.

IT HAS TO SURVIVE. The Deployment page kept `prepared` in React state alone, so a
refresh discarded it: the screen said "No deployment yet" while the backend held a
fully prepared clone, and Chat — gated on that same state — stayed disabled. The agent
was unreachable for a target that was ready.

IT MUST NOT LEAK THE PAT. The stored record carries `pat`, and `repo_url` with the PAT
injected into it, because the agent needs both server-side. An endpoint that returned
the record as stored would hand a personal access token to the browser. That is why
this projects named fields rather than spreading the dict — a spread would quietly
start leaking again the day somebody adds a field.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.config.session_state import (  # noqa: E402
    get_prepared, set_prepared,
)
from shared.routers.deployment_workspace import get_prepared_deploy  # noqa: E402

pytestmark = pytest.mark.unit

TENANT = "t-prepared"
PROJECT = "p-prepared"
SECRET = "super-secret-pat-value"


class _Req:
    class state:  # noqa: N801 - mirrors request.state
        tenant_id = TENANT


@pytest.fixture
def prepared():
    set_prepared(TENANT, PROJECT, {
        "work_dir": "/tmp/clone", "repo_url": f"https://x:{SECRET}@dev.azure.com/acme/_git/sdlc",
        "pat": SECRET,
        "mode": "branch", "ado_project": "sdlc", "repo_name": "sdlc",
        "source_branch": "main", "pr_id": "", "head_sha": "3d9f9f2d",
        "environment": "staging", "deploy_via": "azure_pipelines",
        "image_registry": "", "image_name": "sdlc", "namespace": "",
    })
    yield
    set_prepared(TENANT, PROJECT, {})


# -- it survives ---------------------------------------------------------------


async def test_a_prepared_target_is_returned(prepared):
    out = await get_prepared_deploy(PROJECT, _Req())
    assert out["status"] == "ready"
    assert out["repo_name"] == "sdlc"
    assert out["branch"] == "main"
    assert out["deploy_via"] == "azure_pipelines"
    assert out["head_sha"] == "3d9f9f2d"


async def test_nothing_prepared_is_a_null_status_not_an_error():
    """The empty state is a legitimate answer, including after a backend restart —
    the session is in memory, so the clone on disk is orphaned and unusable anyway."""
    out = await get_prepared_deploy("a-project-never-prepared", _Req())
    assert out["status"] is None


# -- it must not leak ----------------------------------------------------------


async def test_the_pat_is_never_returned(prepared):
    """THE ONE THAT WOULD HURT. The stored record holds the token because the agent
    needs it; the browser does not."""
    out = await get_prepared_deploy(PROJECT, _Req())
    assert SECRET not in str(out), f"the PAT reached the response: {out}"


async def test_the_authenticated_clone_url_is_never_returned(prepared):
    """`repo_url` has the PAT injected into it — returning the URL leaks the token
    just as surely as returning the token."""
    out = await get_prepared_deploy(PROJECT, _Req())
    assert "repo_url" not in out
    assert not any("dev.azure.com/acme/_git" in str(v) for v in out.values())


async def test_the_local_work_dir_is_never_returned(prepared):
    """A server filesystem path is of no use to a browser and tells a reader where the
    platform keeps its clones."""
    out = await get_prepared_deploy(PROJECT, _Req())
    assert "work_dir" not in out
    assert "/tmp/clone" not in str(out)


async def test_it_projects_named_fields_rather_than_spreading_the_record():
    """A spread would start leaking the day somebody adds a field to the stored record.
    Pinned structurally so the safe shape is not quietly widened."""
    import inspect

    src = inspect.getsource(get_prepared_deploy)
    assert "**data" not in src, "the stored record must not be spread into the response"
    assert "data.get(" in src


async def test_the_response_shape_matches_what_prepare_returns(prepared):
    """The page parses both through the same schema, so a missing key would break
    hydration while the endpoint looked fine."""
    out = await get_prepared_deploy(PROJECT, _Req())
    for key in ("status", "mode", "repo_name", "ado_project", "branch", "pr_id",
                "pr_title", "head_sha", "environment", "deploy_via",
                "image_registry", "image_name", "namespace"):
        assert key in out, f"missing {key}"
