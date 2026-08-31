"""The keys removed from config/env.py are gone AND nothing still reaches for them.

Twenty-one settings were deleted from config/env.py across this cleanup:

  Connector credentials  JIRA_URL/EMAIL/API_TOKEN, CONFLUENCE_URL/EMAIL/API_TOKEN,
                         SONARQUBE_URL/TOKEN, SLACK_BOT_TOKEN, ADO_ORG_URL/ADO_PAT,
                         GHA_PAT/GHA_OWNER, MSGRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET,
                         FIGMA_PAT, GITHUB_APP_INSTALLATION_ID
  OAuth apps             JIRA_OAUTH_CLIENT_ID/SECRET, GITHUB_APP_ID,
                         GITHUB_APP_PRIVATE_KEY(_PATH), GITHUB_OAUTH_CLIENT_SECRET,
                         SLACK_CLIENT_ID/SECRET, FIGMA_OAUTH_CLIENT_ID/SECRET
  Webhook secrets        GITHUB_WEBHOOK_SECRET, SLACK_SIGNING_SECRET,
                         JIRA_WEBHOOK_SECRET, ADO_WEBHOOK_USER/PASSWORD,
                         GHA_WEBHOOK_SECRET, MSGRAPH_WEBHOOK_CLIENT_STATE
  Dead knobs             OPENAI_API_KEY, AZURE_OPENAI_*, GOOGLE_API_KEY_DESIGN,
                         ANTHROPIC_MODEL_{FAST,STANDARD,EXTENDED}, SLA_*,
                         WORKER_POOL_CONCURRENCY, DEFAULT_*_BUDGET_USD,
                         PLATFORM_ADMIN_EMAILS/PASSWORD

Deleting a constant is easy to get wrong in a way tests miss: an `from config.env
import X` that no test imports raises ImportError only in production, and a
`getattr(env, "X", default)` degrades silently instead. So this file checks the two
failure modes directly — every module still imports, and no module names a dead key —
rather than trusting that some other test would have noticed.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ENV_PY = BACKEND / "config" / "env.py"

# Directories that hold first-party code. `files/` is the agent work area (cloned
# repos); .venv and caches are not ours.
SOURCE_DIRS = [
    "agents", "agents_orchestrator", "config", "migrations", "scim",
    "scripts", "shared", "tests", "webhooks", "workers", "workflows",
]

REMOVED_KEYS = {
    # Connector credentials
    "JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
    "CONFLUENCE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
    "SONARQUBE_URL", "SONARQUBE_TOKEN",
    "SLACK_BOT_TOKEN", "ADO_ORG_URL", "ADO_PAT",
    "GHA_PAT", "GHA_OWNER",
    "MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET",
    "FIGMA_PAT", "GITHUB_APP_INSTALLATION_ID",
    # OAuth apps
    "JIRA_OAUTH_CLIENT_ID", "JIRA_OAUTH_CLIENT_SECRET",
    "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET",
    "FIGMA_OAUTH_CLIENT_ID", "FIGMA_OAUTH_CLIENT_SECRET",
    # Webhook secrets
    "GITHUB_WEBHOOK_SECRET", "SLACK_SIGNING_SECRET", "JIRA_WEBHOOK_SECRET",
    "ADO_WEBHOOK_USER", "ADO_WEBHOOK_PASSWORD", "GHA_WEBHOOK_SECRET",
    "MSGRAPH_WEBHOOK_CLIENT_STATE",
    # Dead knobs
    "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "GOOGLE_API_KEY_DESIGN",
    "ANTHROPIC_MODEL_FAST", "ANTHROPIC_MODEL_STANDARD", "ANTHROPIC_MODEL_EXTENDED",
    "SLA_REQUIREMENTS_HOURS", "SLA_DESIGN_HOURS", "SLA_DEVELOPMENT_HOURS",
    "SLA_TESTING_HOURS", "SLA_GRACE_MINUTES", "SLA_CLARIFICATION_HOURS",
    "WORKER_POOL_CONCURRENCY",
    "DEFAULT_ORG_BUDGET_USD", "DEFAULT_WORKSPACE_BUDGET_USD", "DEFAULT_PROJECT_BUDGET_USD",
    "PLATFORM_ADMIN_EMAILS", "PLATFORM_ADMIN_PASSWORD",
}

# Modules on the paths the removals touched. If a removed constant were still
# referenced at module scope, importing these is where it would raise.
CRITICAL_MODULES = [
    "config.env",
    "config.secret_bootstrap",
    "config.connector_factory",
    "config.connectors.azure_devops",
    "config.connectors.azure_repos",
    "config.connectors.confluence",
    "config.connectors.figma",
    "config.connectors.github_actions",
    "config.connectors.github_issues",
    "config.connectors.jira",
    "config.connectors.msgraph",
    "config.connectors.msteams",
    "config.connectors.sharepoint",
    "config.connectors.slack",
    "config.connectors.sonarqube",
    "webhooks.router",
    "shared.routers.connectors",
    "shared.services.secret_store",
    "shared.keyvault",
    "scripts.m6_webhook_local_smoke",
]


def _source_files() -> list[Path]:
    out: list[Path] = []
    for d in SOURCE_DIRS:
        root = BACKEND / d
        if not root.is_dir():
            continue
        out += [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    out += [p for p in BACKEND.glob("*.py")]
    return out


@pytest.mark.unit
def test_every_removed_key_is_actually_gone_from_env_py():
    """The constants are not merely unused — they are not defined."""
    src = ENV_PY.read_text(encoding="utf-8")
    defined = set(re.findall(r"^([A-Z][A-Z0-9_]+)\s*(?::[^=]+)?=", src, re.M))
    still_there = sorted(defined & REMOVED_KEYS)
    assert not still_there, f"config/env.py still defines {still_there}"


@pytest.mark.unit
def test_env_py_no_longer_reads_any_removed_key():
    """A constant can be deleted while the os.environ.get() line survives under a new
    name. Checking the READS, not just the assignments, catches that."""
    src = ENV_PY.read_text(encoding="utf-8")
    read = set(
        re.findall(
            r'os\.(?:environ(?:\.get)?|getenv)\(\s*["\']([A-Za-z][A-Za-z0-9_]+)["\']',
            src,
        )
    )
    still_read = sorted(read & REMOVED_KEYS)
    assert not still_read, f"config/env.py still reads {still_read} from the environment"


@pytest.mark.unit
@pytest.mark.parametrize("module", CRITICAL_MODULES)
def test_module_still_imports(module):
    """`from config.env import GONE` is an ImportError at import time and nowhere else.

    Every module on a path the removals touched is imported here, so a dangling import
    fails the build instead of the first request that happens to reach it.
    """
    importlib.import_module(module)


@pytest.mark.unit
def test_nothing_imports_a_removed_key_from_config_env():
    """The direct breakage: `from config.env import <removed>` anywhere in the tree."""
    offenders: list[str] = []
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # not ours to fix here
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "config.env":
                for alias in node.names:
                    if alias.name in REMOVED_KEYS:
                        offenders.append(f"{path.relative_to(BACKEND)}: {alias.name}")
    assert not offenders, "removed config.env names are still imported:\n" + "\n".join(offenders)


@pytest.mark.unit
def test_nothing_reads_a_removed_key_from_the_environment_directly():
    """The quiet breakage.

    config/env.py's own docstring says "do not call os.getenv elsewhere". A module that
    ignored that and read JIRA_API_TOKEN straight from os.environ would not raise when
    the constant was deleted — it would just silently resolve to "" forever, which is
    exactly the platform-wide credential rung this cleanup removed, reintroduced by a
    different route.

    tests/ is excluded: a test may legitimately set a removed key to prove it is
    ignored, and this file's own REMOVED_KEYS list would otherwise match itself.
    """
    pattern = re.compile(
        r'os\.(?:environ(?:\.get)?|getenv)\(\s*["\']([A-Za-z][A-Za-z0-9_]+)["\']'
    )
    offenders: list[str] = []
    for path in _source_files():
        rel = path.relative_to(BACKEND)
        if "tests" in rel.parts or rel.name.startswith("test_"):
            continue
        for key in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
            if key in REMOVED_KEYS:
                offenders.append(f"{rel}: {key}")
    assert not offenders, (
        "removed keys are still read straight from the environment:\n" + "\n".join(offenders)
    )


@pytest.mark.unit
def test_the_platform_secret_list_carries_no_connector_secret():
    """secret_bootstrap hydrates PLATFORM_SECRETS into os.environ when ENV != dev.

    A connector secret left on that list would be written back into the process
    environment at boot — a platform-wide value again, arriving by the one route that
    survives every check on the connectors themselves.
    """
    from config import secret_bootstrap as sb

    leaked = sorted(set(sb.PLATFORM_SECRETS) & REMOVED_KEYS)
    assert not leaked, f"PLATFORM_SECRETS still hydrates {leaked} into os.environ"


@pytest.mark.unit
def test_setting_a_removed_key_in_the_environment_changes_nothing(monkeypatch):
    """An operator with a stale .env must get the new behaviour, not the old one.

    Re-importing config.env with every removed key set to a recognisable value proves
    none of them is still consulted under any name.
    """
    import config.env as env

    for key in sorted(REMOVED_KEYS):
        monkeypatch.setenv(key, f"STALE-{key}")
    reloaded = importlib.reload(env)
    try:
        leaked = [
            name
            for name in dir(reloaded)
            if isinstance(getattr(reloaded, name), str)
            and getattr(reloaded, name).startswith("STALE-")
        ]
        assert not leaked, f"stale environment values reached {leaked}"
    finally:
        # Leave the module as the rest of the session expects to find it.
        for key in REMOVED_KEYS:
            monkeypatch.delenv(key, raising=False)
        importlib.reload(env)
