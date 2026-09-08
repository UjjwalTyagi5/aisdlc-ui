"""One way to ask for a repository, whoever hosts it.

THE PROBLEM THIS SOLVES. `shared/services/ado_repos` is called DIRECTLY from more than
ten places — the documentation, deployment, development, testing and code-review
workspaces, and four routers besides. Every one of them hard-codes Azure DevOps. A
tenant on GitHub could connect GitHub Issues and GitHub Actions and still not open a
workspace anywhere, because nothing in the codebase could clone from anything else.

Rewriting eleven call sites to branch on a provider would put the same `if ado: ... else:
...` in eleven places, and the eleventh would be written differently from the first. This
is the single place that branches.

THE PROVIDER IS DISCOVERED, NOT CONFIGURED. `available()` asks each backend whether this
tenant/project/user actually has a credential for it, in the same project-scoped way
`resolve_auth` does everywhere else. A project with only Azure DevOps connected behaves
exactly as it did before this module existed; one with both is asked which to use; one
with neither gets a single clear sentence instead of a 500 from whichever service was
reached first.

ADO's PROJECT LAYER HAS NO GITHUB EQUIVALENT, and `namespace` is the word this module
uses for the thing that differs: an ADO project, or a GitHub owner. Callers pass whatever
their picker showed. `list_namespaces` is what fills that picker.

CLONING IS SHARED. `ado_repos.clone_into` is plain git over HTTPS with a credential in
the URL, and its `PersonalAccessToken:{secret}@host` form is accepted by GitHub as well —
so there is one clone implementation and no provider branch in the part that touches the
filesystem.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

Provider = Literal["ado", "github"]

#: Ordered, and the order is the tie-break when a project has both. Azure DevOps first
#: because every existing project on this platform is on it — a tenant that adds GitHub
#: must not have its established workspaces silently switch hosts.
_ORDER: tuple[Provider, ...] = ("ado", "github")

NOT_CONFIGURED = (
    "No source-code connector is configured for this project. Connect Azure DevOps or "
    "GitHub on the Integrations page."
)


def _backend(provider: Provider):
    if provider == "github":
        from shared.services import github_repos  # noqa: PLC0415

        return github_repos
    from shared.services import ado_repos  # noqa: PLC0415

    return ado_repos


async def available(
    tenant_id: str, *, project_id: str = "", owner_id: str = "",
) -> list[Provider]:
    """Which providers this caller actually holds a credential for.

    Asked per provider rather than read from a setting, because the credential is the
    only thing that decides whether a clone can happen — a connector row with no usable
    token would otherwise offer a provider that fails at the last step.
    """
    found: list[Provider] = []
    for provider in _ORDER:
        try:
            base, secret = await _backend(provider).resolve_auth(
                tenant_id, project_id=project_id, owner_id=owner_id
            )
            if base and secret:
                found.append(provider)
        except Exception:  # noqa: BLE001 — an unconfigured provider is not an error
            continue
    return found


async def resolve(
    tenant_id: str, *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> tuple[Provider, str, str]:
    """(provider, base, secret) for the provider to use, or raise with a clear reason.

    An explicit `provider` is honoured and its absence of credentials is an error — the
    caller named it, so silently using the other one would clone from a host they did not
    ask for. With none named, the first available in `_ORDER` wins.
    """
    if provider:
        if provider not in _ORDER:
            raise RuntimeError(f"Unknown source provider {provider!r}.")
        base, secret = await _backend(provider).resolve_auth(  # type: ignore[arg-type]
            tenant_id, project_id=project_id, owner_id=owner_id
        )
        if not (base and secret):
            raise RuntimeError(
                f"{'GitHub' if provider == 'github' else 'Azure DevOps'} is not "
                "configured for this project. Connect it on the Integrations page."
            )
        return provider, base, secret  # type: ignore[return-value]

    for candidate in _ORDER:
        try:
            base, secret = await _backend(candidate).resolve_auth(
                tenant_id, project_id=project_id, owner_id=owner_id
            )
        except Exception:  # noqa: BLE001
            continue
        if base and secret:
            return candidate, base, secret
    raise RuntimeError(NOT_CONFIGURED)


async def list_namespaces(
    tenant_id: str, *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> tuple[Provider, list[dict]]:
    """The ADO projects, or the GitHub owners, this caller can reach."""
    chosen, base, secret = await resolve(
        tenant_id, project_id=project_id, owner_id=owner_id, provider=provider)
    backend = _backend(chosen)
    if chosen == "github":
        return chosen, await backend.list_projects(token=secret)
    return chosen, await backend.list_projects(pat=secret, org_url=base)


async def list_repos(
    tenant_id: str, namespace: str, *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> tuple[Provider, list[dict]]:
    chosen, base, secret = await resolve(
        tenant_id, project_id=project_id, owner_id=owner_id, provider=provider)
    backend = _backend(chosen)
    if chosen == "github":
        return chosen, await backend.list_repos(namespace, token=secret)
    return chosen, await backend.list_repos(namespace, pat=secret, org_url=base)


async def list_branches(
    tenant_id: str, namespace: str, repo: str, *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> tuple[Provider, list[dict]]:
    chosen, base, secret = await resolve(
        tenant_id, project_id=project_id, owner_id=owner_id, provider=provider)
    backend = _backend(chosen)
    if chosen == "github":
        return chosen, await backend.list_branches(namespace, repo, token=secret)
    return chosen, await backend.list_branches(namespace, repo, pat=secret, org_url=base)


async def list_pull_requests(
    tenant_id: str, namespace: str, repo: str, *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> tuple[Provider, list[dict]]:
    chosen, base, secret = await resolve(
        tenant_id, project_id=project_id, owner_id=owner_id, provider=provider)
    backend = _backend(chosen)
    if chosen == "github":
        return chosen, await backend.list_pull_requests(namespace, repo, token=secret)
    return chosen, await backend.list_pull_requests(
        namespace, repo, pat=secret, org_url=base)


async def get_pull_request(
    tenant_id: str, namespace: str, repo: str, number: str,
    *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> tuple[Provider, dict | None]:
    chosen, base, secret = await resolve(
        tenant_id, project_id=project_id, owner_id=owner_id, provider=provider)
    backend = _backend(chosen)
    if chosen == "github":
        return chosen, await backend.get_pull_request(namespace, repo, number, token=secret)
    return chosen, await backend.get_pull_request(
        namespace, repo, number, pat=secret, org_url=base)


async def clone(
    tenant_id: str, namespace: str, repo: str, branch: str, work_dir: str,
    *, project_id: str = "", owner_id: str = "",
    provider: Provider | str | None = None,
) -> dict[str, Any]:
    """Clone a branch read-only into `work_dir`, whichever host it lives on.

    One implementation for both: `ado_repos.clone_into` is plain git over HTTPS with the
    credential in the URL, and its `PersonalAccessToken:{secret}@host` form is what
    GitHub accepts too. A second copy of the clone-and-scrub logic is the last thing
    this needs — that function also redacts the secret from git's output, and a
    provider-specific twin would be the one that forgot.
    """
    import asyncio  # noqa: PLC0415

    from shared.services import ado_repos  # noqa: PLC0415

    chosen, base, secret = await resolve(
        tenant_id, project_id=project_id, owner_id=owner_id, provider=provider)
    backend = _backend(chosen)

    if chosen == "github":
        remote = await backend.resolve_clone_url(namespace, repo, token=secret)
    else:
        remote = await backend.resolve_clone_url(
            namespace, repo, pat=secret, org_url=base)
    if not remote:
        raise RuntimeError(
            f"Repository {repo!r} was not found under {namespace!r} on "
            f"{'GitHub' if chosen == 'github' else 'Azure DevOps'}."
        )

    result = await asyncio.to_thread(
        ado_repos.clone_into, work_dir, remote, branch, secret)
    result["provider"] = chosen
    result["remote_url"] = remote
    return result
