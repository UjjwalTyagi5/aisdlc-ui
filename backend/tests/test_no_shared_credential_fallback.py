"""A personal connector has exactly ONE credential rung: the acting user's own.

THE BUG THIS CLOSES, reported from the product. A project was created whose members had
added no Azure DevOps credential — the Integrations page said "Needs a credential" on
all three connectors — and the agent reached Azure DevOps anyway, because a tenant-wide
PAT existed somewhere else. Two things were wrong with that at once:

  it worked      a project that never configured a connector could still use it, so
                 "Needs a credential" was decoration rather than a gate;
  it lied        the external system records whoever minted the shared token, so the
                 work was attributed to a person who did not do it.

"It works but attributes wrongly" is far harder to notice than "it does not work",
which is why the fallback had to go rather than be documented.

WHAT IS EXEMPT, and not out of laziness. ms_teams, sharepoint and msgraph authenticate
as an app registration — there is no personal credential to fall back TO. Slack's bot
token identifies an app in a workspace, every member would paste the same value, and it
is what notify_dispatch sends with, which has no user at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.connectors.base import PERSONAL_CREDENTIAL_KINDS  # noqa: E402

pytestmark = pytest.mark.unit

TENANT = "some-tenant"


def _build(kind: str):
    from config.connector_factory import _CONNECTOR_REGISTRY, _load_connector_class

    cls = _load_connector_class(_CONNECTOR_REGISTRY[kind])
    try:
        return cls(org_url="https://example.test", tenant_id=TENANT)
    except TypeError:
        c = cls()
        c._tenant_id = TENANT
        return c


def _credential(auth: dict) -> str:
    """Whatever this connector calls its secret."""
    for key in ("pat", "token", "api_token", "bot_token", "access_token"):
        if auth.get(key):
            return str(auth[key])
    return ""


# -- the rule ------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(PERSONAL_CREDENTIAL_KINDS))
async def test_no_personal_credential_means_not_connected(kind, monkeypatch):
    """THE INVARIANT. Without the acting user's own credential there is nothing to
    authenticate with — not a borrowed tenant token."""
    # A tenant-wide SECRET must not be reachable. Tenant-wide CONFIG still is — a site
    # URL, an email, a repo owner are not credentials, and a project pointing at the
    # organisation's Jira instance is the intended behaviour.
    secretish = ("token", "pat", "secret", "key", "password")

    async def _tenant_secret(name, tenant_id=None):
        if any(s in (name or "").lower() for s in secretish):
            return f"TENANT-WIDE-{name}"      # offered, and must be refused
        return None

    monkeypatch.setattr("shared.keyvault.load_secret", _tenant_secret)
    conn = _build(kind)

    async def _none(_tenant, _target):
        return None

    monkeypatch.setattr(conn, "_resolve_credential_override", _none)
    auth = await conn.auth_adapter(TENANT)
    assert _credential(auth) == "", (
        f"{kind} produced a credential with none saved for the user: it would work for "
        f"a project that never configured it, and attribute the work to whoever minted "
        f"that token"
    )


@pytest.mark.parametrize("kind", sorted(PERSONAL_CREDENTIAL_KINDS))
def test_every_personal_connector_declares_itself_as_one(kind):
    """The flag and the constant must agree, or a connector silently keeps its rung."""
    assert _build(kind)._tenant_fallback_allowed() is False


@pytest.mark.parametrize("kind", ["ms_teams", "sharepoint", "slack"])
def test_app_identity_connectors_keep_their_rung(kind):  # noqa: D401
    """Not an oversight. These authenticate as an app registration or a workspace bot;
    there is no personal credential for them to fall back TO, and removing the rung
    would delete the feature rather than secure it."""
    from config.connector_factory import _CONNECTOR_REGISTRY

    if kind not in _CONNECTOR_REGISTRY:
        pytest.skip(f"{kind} is not a registered connector kind here")
    assert _build(kind)._tenant_fallback_allowed() is True


def test_the_exemptions_are_exactly_the_app_identity_ones():
    """A connector quietly added to the exemption list is a fallback quietly restored."""
    from config.connector_factory import _CONNECTOR_REGISTRY

    registered = {
        k for k in _CONNECTOR_REGISTRY
        if k not in ("github",)          # an alias of github_issues, same connector
    }
    exempt = sorted(registered - PERSONAL_CREDENTIAL_KINDS)
    assert exempt == ["ms_teams", "sharepoint", "slack"], (
        f"the exemption list changed: {exempt}. Each exemption is a connector that can "
        "act without naming a person — it needs a reason, in this test."
    )


# -- the org-wide door is shut, not merely unused -------------------------------


@pytest.mark.parametrize("kind", sorted(PERSONAL_CREDENTIAL_KINDS))
def test_the_org_wide_credential_route_refuses_personal_connectors(kind):
    """Removing the RUNG without refusing the WRITE is the worse half of the fix.

    The form would report success, the secret would sit encrypted in the database, and
    nothing would ever read it — a credential somebody believes they configured. The
    route now refuses, and the refusal says where the credential does belong.
    """
    import inspect

    from shared.routers import connectors as mod

    src = inspect.getsource(mod.set_connector_credentials)
    assert "PERSONAL_CREDENTIAL_KINDS" in src, (
        "the org-wide credential route no longer checks for personal connectors"
    )
    assert "personal_credential_only" in src


def test_the_refusal_tells_the_caller_where_to_go_instead():
    """A 400 that only says no leaves somebody with a token and nowhere to put it."""
    import inspect

    from shared.routers import connectors as mod

    src = inspect.getsource(mod.set_connector_credentials)
    assert "project's Integrations page" in src
