"""No workspace route decides for itself which host the code lives on.

THE BUG THIS ENDS. `repo_source` exists so exactly one module branches on the provider.
It was written, and then five workspace routes went on calling `ado_repos` directly —
so Documentation and Deployment learned to clone from GitHub while Code Review,
Security and Development stayed Azure DevOps only. The dialogs looked identical. The
difference showed up as "no repositories found" on a project whose code was sitting on
GitHub, with nothing to say which of the two halves had not been wired.

It is the kind of gap that reappears every time somebody adds a workspace: `ado_repos`
is imported in the file already, its functions do the obvious thing, and the resulting
route works perfectly for the tenant who happens to be on Azure DevOps.

WHAT IS STILL ALLOWED, and why the check is about specific functions rather than the
import: `ado_repos.WORKSPACE_ROOT` is where every workspace lives whoever hosts it, and
`clone_into` / `clone_and_diff` are plain git that `repo_source` itself delegates to.
Only the calls that RESOLVE A HOST are forbidden here.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.unit

#: Every router that opens a workspace on somebody's repository.
WORKSPACE_ROUTERS = [
    "shared/routers/dev_workspace.py",
    "shared/routers/code_review_workspace.py",
    "shared/routers/security_workspace.py",
    "shared/routers/documentation_workspace.py",
    "shared/routers/deployment_workspace.py",
]

#: `ado_repos` functions that answer "which host, and with whose credential". Calling
#: any of these from a route is choosing Azure DevOps on the tenant's behalf.
HOST_DECIDING = {
    "resolve_auth",
    "resolve_clone_url",
    "list_projects",
    "list_repos",
    "list_branches",
    "list_pull_requests",
    "get_pull_request",
}


def _ado_calls(path: pathlib.Path) -> list[tuple[str, int]]:
    """`ado_repos.<host-deciding>(...)` calls in this file, with line numbers.

    Parsed, not grepped: an argument list spanning several lines is invisible to a
    line-based search, and a guard that is easy to fool is worse than none.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in HOST_DECIDING:
            continue
        owner = getattr(fn.value, "id", None)
        if owner == "ado_repos":
            found.append((fn.attr, node.lineno))
    return found


@pytest.mark.parametrize("rel", WORKSPACE_ROUTERS)
def test_a_workspace_route_never_picks_the_host_itself(rel: str):
    offenders = _ado_calls(ROOT / rel)

    assert not offenders, (
        f"{rel} resolves a repository host directly:\n  "
        + "\n  ".join(f"ado_repos.{name}() at line {line}" for name, line in offenders)
        + "\n\nGo through shared/services/repo_source instead, so a GitHub project "
          "reaches the same route the Azure DevOps one does."
    )


def test_the_check_can_actually_see_such_a_call():
    """NON-VACUITY. An AST walk that matched nothing would pass every case above while
    the routes went back to Azure DevOps one at a time."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "sample.py"
        p.write_text(
            "async def prepare():\n"
            "    org_url, pat = await ado_repos.resolve_auth(\n"
            "        tenant_id, project_id=project_id,\n"
            "    )\n",
            encoding="utf-8",
        )
        assert _ado_calls(p) == [("resolve_auth", 2)]


def test_the_facade_is_what_they_call_instead():
    """The other half of the same property: a route that resolves nothing at all would
    satisfy the test above by doing no work."""
    missing = [
        rel for rel in WORKSPACE_ROUTERS
        if "repo_source" not in (ROOT / rel).read_text(encoding="utf-8-sig", errors="replace")
    ]

    assert not missing, f"these open a workspace without going through the facade: {missing}"
