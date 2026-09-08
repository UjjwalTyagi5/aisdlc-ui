"""One way to ask for a repository, whoever hosts it.

WHY THIS ABSTRACTION EXISTS. `shared/services/ado_repos` is called directly from more
than ten places — the documentation, deployment, development, testing and code-review
workspaces, and four routers besides — and every one of them hard-codes Azure DevOps. A
tenant whose code is on GitHub could connect GitHub Issues and GitHub Actions and still
open no workspace anywhere, because nothing in the codebase could clone from anything
else: `ado_repos` is a service, not a connector, and it was the only one.

Branching on the provider at eleven call sites would put the same `if` in eleven places,
and the eleventh would be written differently from the first. `repo_source` is the one
place that branches, and these tests are about the decisions it makes — which provider,
and what happens when the answer is "neither" or "not the one you asked for".

The GitHub backend's own quirks are tested here too, because they are exactly what a
second provider gets wrong: two incompatible credential shapes, and a namespace concept
that does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services import repo_source as rs  # noqa: E402


class _Backend:
    """Stands in for ado_repos / github_repos: only `resolve_auth` matters here."""

    def __init__(self, base="https://host", secret="s"):
        self._answer = (base, secret)

    async def resolve_auth(self, tenant_id="", *, project_id="", owner_id=""):
        return self._answer


def _wire(monkeypatch, *, ado=None, github=None):
    """Point the façade at stub backends without touching the real services."""
    def _pick(provider):
        return {"ado": ado, "github": github}[provider] or _Backend("", "")

    monkeypatch.setattr(rs, "_backend", _pick)


@pytest.mark.asyncio
async def test_a_project_with_only_azure_is_unchanged(monkeypatch):
    """THE PROMISE TO EVERY EXISTING PROJECT. All of them are on Azure DevOps; adding a
    second provider must not move any of them."""
    _wire(monkeypatch, ado=_Backend(), github=_Backend("", ""))

    assert await rs.available("t") == ["ado"]
    assert (await rs.resolve("t"))[0] == "ado"


@pytest.mark.asyncio
async def test_a_project_with_only_github_uses_it(monkeypatch):
    """The case that was impossible before: no Azure DevOps anywhere, and a workspace
    still opens."""
    _wire(monkeypatch, ado=_Backend("", ""), github=_Backend())

    assert await rs.available("t") == ["github"]
    assert (await rs.resolve("t"))[0] == "github"


@pytest.mark.asyncio
async def test_azure_wins_the_tie(monkeypatch):
    """A tenant that ADDS GitHub must not have its established workspaces quietly
    switch hosts. The order is the tie-break and it favours what was already there."""
    _wire(monkeypatch, ado=_Backend(), github=_Backend())

    assert await rs.available("t") == ["ado", "github"]
    assert (await rs.resolve("t"))[0] == "ado"


@pytest.mark.asyncio
async def test_an_explicit_provider_is_honoured_over_the_default(monkeypatch):
    """Once a picker exists, the user's choice has to beat the tie-break."""
    _wire(monkeypatch, ado=_Backend(), github=_Backend())

    assert (await rs.resolve("t", provider="github"))[0] == "github"


@pytest.mark.asyncio
async def test_naming_a_provider_you_do_not_have_is_an_error(monkeypatch):
    """NOT a silent fallback to the other one. They asked for GitHub; cloning from
    Azure DevOps instead would hand them somebody else's code and call it success."""
    _wire(monkeypatch, ado=_Backend(), github=_Backend("", ""))

    with pytest.raises(RuntimeError, match="GitHub is not configured"):
        await rs.resolve("t", provider="github")


@pytest.mark.asyncio
async def test_no_source_at_all_says_so_once(monkeypatch):
    """One sentence naming both options, rather than a 500 from whichever service
    happened to be reached first — which is what every caller did before."""
    _wire(monkeypatch, ado=_Backend("", ""), github=_Backend("", ""))

    assert await rs.available("t") == []
    with pytest.raises(RuntimeError, match="Connect Azure DevOps or GitHub"):
        await rs.resolve("t")


@pytest.mark.asyncio
async def test_an_unknown_provider_is_refused_rather_than_defaulted(monkeypatch):
    """A typo in a request body must not silently clone from Azure DevOps."""
    _wire(monkeypatch, ado=_Backend(), github=_Backend())

    with pytest.raises(RuntimeError, match="Unknown source provider"):
        await rs.resolve("t", provider="gitlab")


@pytest.mark.asyncio
async def test_a_backend_that_raises_does_not_hide_the_other_one(monkeypatch):
    """`available` is a discovery call: one provider erroring must not make the whole
    project look sourceless."""
    class _Broken:
        async def resolve_auth(self, *a, **k):
            raise RuntimeError("connector exploded")

    _wire(monkeypatch, ado=_Broken(), github=_Backend())

    assert await rs.available("t") == ["github"]


# -- the GitHub backend's own quirks -----------------------------------------


@pytest.mark.asyncio
async def test_github_reads_repositories_from_either_credential_shape(monkeypatch):
    """THE BUG THIS CAUGHT IN THE FIELD. GitHub issues two things this platform may
    hold and they are not interchangeable: an App INSTALLATION token, which answers
    `/installation/repositories` and 403s on `/user/repos`, and a personal access token,
    which is the opposite. The first implementation assumed App tokens and got

        "You must authenticate with an installation access token..."

    against a tenant using a fine-grained PAT. Both shapes now work."""
    from shared.services import github_repos as gh

    calls: list[str] = []

    async def _get(path, token, params=None):
        calls.append(path)
        if path == "/installation/repositories":
            raise RuntimeError("403 — not an installation token")
        return [{"name": "app", "owner": {"login": "acme"}, "id": 1,
                 "default_branch": "main", "clone_url": "https://github.com/acme/app.git"}]

    monkeypatch.setattr(gh, "_get", _get)

    repos = await gh._visible_repos("tok")

    assert [r["name"] for r in repos] == ["app"]
    assert "/installation/repositories" in calls and "/user/repos" in calls


@pytest.mark.asyncio
async def test_github_owners_come_from_repositories_not_memberships(monkeypatch):
    """Membership of an organisation is not permission over its repositories. Offering
    an owner whose repos then come back empty sends somebody looking for a repository
    that was never shared with them."""
    from shared.services import github_repos as gh

    async def _visible(_tok):
        return [
            {"owner": {"login": "acme"}}, {"owner": {"login": "acme"}},
            {"owner": {"login": "personal"}},
        ]

    monkeypatch.setattr(gh, "_visible_repos", _visible)

    owners = await gh.list_projects(token="tok")

    assert [o["name"] for o in owners] == ["acme", "personal"]


@pytest.mark.asyncio
async def test_github_returns_the_same_row_shape_as_azure(monkeypatch):
    """The façade dispatches without translating, so the two backends must already
    agree. A key named differently here becomes a KeyError in a dialog."""
    from shared.services import github_repos as gh

    async def _visible(_tok):
        return [{"id": 7, "name": "app", "owner": {"login": "acme"},
                 "default_branch": "main", "clone_url": "https://github.com/acme/app.git"}]

    monkeypatch.setattr(gh, "_visible_repos", _visible)

    rows = await gh.list_repos("acme", token="tok")

    assert set(rows[0]) == {"id", "name", "default_branch", "remote_url"}
