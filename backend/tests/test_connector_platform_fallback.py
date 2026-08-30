"""A tenant must never transact using PLATFORM-wide connector credentials.

Every connector's auth_adapter used to walk four rungs:

    tenant secret_store -> KV {tenant}-{ref} -> KV {ref} (global) -> env var

The last two are not tenant-scoped. They were introduced for local development and
each carried a comment reading "never use in production" — but nothing enforced it.
In an enterprise deployment a tenant who never connected Jira silently transacted
with the PLATFORM's Jira token: another tenant's data, billed and audited as theirs,
with no grant anywhere in the RBAC model authorising it.

Both rungs are now GONE rather than gated. A tenant with no credential of its own
resolves to None and fails as "not connected". This file pins that structurally —
the earlier version tested a runtime guard (`_platform_fallback.env_credential`)
that no longer needs to exist, because there is nothing left to guard.

There are now NO exceptions. github_issues' `github-app-id` / `github-app-private-key`
were the last, defended as platform-level by construction. The App is; its private key
is not safe to share, because it signs as the App across every installation the App
has. A tenant using GitHub App auth stores its own App id and key tenant-scoped.

The OAuth client secrets that used to sit alongside them in config/env.py are gone
too, along with the 3LO flow that read them — see the note in config/env.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CONNECTOR_DIR = Path(__file__).resolve().parents[1] / "config" / "connectors"

# Modules that resolve tenant credentials. Support modules (base, models, http_client,
# rate_limit, router, scoped, context) hold no ladders.
CONNECTOR_MODULES = [
    "azure_devops",
    "azure_repos",
    "confluence",
    "figma",
    "github_actions",
    "github_issues",
    "jira",
    "msgraph",
    "msteams",
    "sharepoint",
    "slack",
    "sonarqube",
]

# EMPTY. There is no longer a single ref any connector may read without a tenant.
# github-app-id / github-app-private-key were the last exception, on the grounds that
# a GitHub App is platform-level by construction. That is true of the App, but a
# platform-wide App private key signs as the App across EVERY installation it has —
# the same cross-tenant reach the env credentials had, in a shape that looked
# structural. A tenant that wants App auth registers its own App and stores the id and
# key under its own tenant scope, exactly like every other credential.
ALLOWED_PLATFORM_REFS: set[str] = set()

# ALSO EMPTY. A connector may import NOTHING from config.env. Process configuration is
# the same value for every tenant by definition, so there is no credential-adjacent
# thing a connector can correctly read from it.
ALLOWED_ENV_IMPORTS: set[str] = set()

# Constructor parameters that would hand a connector a credential to hold. A connector
# takes routing context (org_url, app_id, installation_id, tenant_id) and resolves the
# credential per call; anything below is a value that lives on the instance for its
# whole lifetime, which is what REQ-M6-14 forbids.
#
# SlackConnector had `bot_token=""` here, documented as a local-dev hint that Key Vault
# would override. Nothing in the application ever passed it — only tests — so it was a
# credential rung kept alive purely by its own test coverage.
CREDENTIAL_CTOR_PARAMS = {
    "token", "bot_token", "pat", "api_token", "api_key", "password",
    "secret", "client_secret", "private_key", "access_token",
}

# `load_secret("some-ref")` with no tenant_id= kwarg — the global vault rung.
_UNTENANTED_LOAD = re.compile(r'load_secret\(\s*["\']([a-z0-9-]+)["\']\s*\)')


def _source(module: str) -> str:
    path = CONNECTOR_DIR / f"{module}.py"
    assert path.exists(), f"{path} is missing — update CONNECTOR_MODULES"
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
@pytest.mark.parametrize("module", CONNECTOR_MODULES)
def test_no_untenanted_key_vault_rung(module):
    """load_secret(ref) without tenant_id= reads a PLATFORM secret."""
    offenders = [
        ref for ref in _UNTENANTED_LOAD.findall(_source(module))
        if ref not in ALLOWED_PLATFORM_REFS
    ]
    assert not offenders, (
        f"{module}.py resolves {offenders} from the global vault with no tenant "
        f"scope — a tenant that never connected would borrow the platform's."
    )


@pytest.mark.unit
@pytest.mark.parametrize("module", CONNECTOR_MODULES)
def test_no_credential_imported_from_env(module):
    """A connector may not read a tenant credential out of process configuration."""
    tree = ast.parse(_source(module))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "config.env"
        for alias in node.names
    }
    offenders = sorted(imported - ALLOWED_ENV_IMPORTS)
    assert not offenders, (
        f"{module}.py imports {offenders} from config.env. Tenant credentials come "
        f"from the tenant secret store or that tenant's Key Vault secret — never "
        f"from an env var shared by every tenant on the platform."
    )


@pytest.mark.unit
@pytest.mark.parametrize("module", CONNECTOR_MODULES)
def test_no_credential_accepted_as_a_constructor_argument(module):
    """A connector must not be constructible WITH a credential to keep on `self`."""
    tree = ast.parse(_source(module))
    offenders = sorted(
        {
            arg.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            for arg in node.args.args + node.args.kwonlyargs
            if arg.arg in CREDENTIAL_CTOR_PARAMS
        }
    )
    assert not offenders, (
        f"{module}.py's __init__ accepts {offenders}. A credential passed at "
        f"construction time is held for the connector's whole lifetime; resolve it "
        f"inside auth_adapter() per call and discard it (REQ-M6-14)."
    )


@pytest.mark.unit
def test_the_retired_guard_is_gone():
    """_platform_fallback gated the env rung; the rung itself is now removed."""
    assert not (CONNECTOR_DIR / "_platform_fallback.py").exists(), (
        "_platform_fallback.py is back. If a connector needs it again, an env-var "
        "credential rung has been reintroduced — that is the thing to remove."
    )
