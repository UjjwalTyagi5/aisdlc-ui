"""Populate os.environ from Azure Key Vault before anything reads it.

WHY THIS SHAPE. `config/env.py` reads roughly a hundred settings with plain
`os.environ.get(...)` calls, and every module in the codebase imports the constants it
produces. Routing each secret through a resolver would have meant touching every one of
those call sites and every test that patches them. Instead this runs ONCE, immediately
after `load_dotenv()` and before the first constant is read, and writes the values into
`os.environ` itself — so `env.py` is unchanged, and so is everything downstream.

THE SWITCH IS `ENV`. `ENV=dev` (the default) reads everything from `.env` and never
contacts Azure: no credential, no network, no az login, and the test suite and a laptop
with no Azure access behave exactly as they did before. Any other value means the
secrets live in Key Vault and `.env` is not trusted to hold them.

FAIL CLOSED, AND THIS IS THE IMPORTANT PART. When `ENV != dev` and the vault cannot be
reached, this raises and the process does not start. The alternative — carry on with
whatever `.env` happened to contain — is how a production API ends up running on
`JWT_SECRET_KEY="change-me-in-production"`, signing tokens anyone can forge, with
nothing in the logs that looks like an incident. A refusal to boot is loud, immediate,
and cannot be mistaken for working.

An individual secret being ABSENT is not a failure. A connector nobody has configured
has no token, and demanding one would make every optional integration mandatory. Only
the vault itself being unreachable is fatal.

THE DATABASE DSNs ARE DELIBERATELY NOT HERE. `shared/db.py`, `config/checkpoint.py` and
`migrations/env.py` already resolve them from Key Vault under their own
`KV_SECRET_POSTGRES_*` names, and that path predates this module. Two mechanisms writing
the same value would differ the moment one convention changed, and the resulting bug —
an app talking to one database while migrations talk to another — is not one anybody
should have to diagnose.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# The value of ENV that means "everything comes from .env".
DEV_ENV = "dev"

# Which settings are SECRETS. Not every setting — a Jira URL, a model name and a timeout
# are configuration, they belong in .env in every environment, and putting them in a
# vault only makes them harder to change.
#
# The test is "would this appear in an incident report if it leaked": credentials,
# tokens, signing keys, webhook shared secrets. `SMTP_USERNAME` is on the list because
# for Azure Communication Services it embeds the tenant and application ids, which are
# not secret individually but identify the subscription when taken together.
PLATFORM_SECRETS: tuple[str, ...] = (
    # Platform crypto — the two that matter most.
    "JWT_SECRET_KEY",
    "SECRET_STORE_KEY",
    # Infrastructure that carries a password in its URL.
    "REDIS_URL",
    # Model providers.
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "LITELLM_API_KEY",
    # Observability.
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    # Outbound email.
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    # NO CONNECTOR SECRETS. Not one — not a connector credential, not an OAuth client
    # secret, not a webhook signing secret. Everything a connector needs is per-tenant
    # and lives in that tenant's secret store, so hydrating any of it into os.environ
    # would recreate the platform-wide fallback the connectors had removed.
    #
    # The ten that used to be listed here (the OAuth client secrets, the GitHub App
    # private key and the webhook signing secrets) were removed along with the flows
    # that read them. See the long note in config/env.py for why neither group was the
    # platform-level exception it claimed to be.
)

# How long the whole hydration may take before the process gives up and refuses to boot.
# Generous, because this happens once and a slow start beats a wrong one.
_TIMEOUT_SECONDS = 30

# Parallel reads. Key Vault has no batch API, so ~30 secrets fetched one at a time is
# ~30 round trips; at a few hundred milliseconds each that is a visibly slow start for
# no reason. Eight at a time keeps it near the cost of the slowest single read while
# staying well under any throttling threshold.
_MAX_WORKERS = 8


def current_env() -> str:
    """The deployment environment, lowercased. Defaults to dev.

    Defaulting to dev is deliberate: a missing ENV must not silently put a laptop or a
    CI runner into the Key Vault path, where it would fail closed on a vault it was
    never meant to reach. Production sets ENV explicitly.
    """
    return (os.environ.get("ENV") or DEV_ENV).strip().lower()


def is_dev() -> bool:
    return current_env() == DEV_ENV


def secret_name_for(env_var: str, env: str | None = None, prefix: str | None = None) -> str:
    """Env var name -> Key Vault secret name. `JWT_SECRET_KEY` -> `sdlc-prod-jwt-secret-key`.

    Key Vault permits only alphanumerics and dashes, so underscores become dashes and
    the whole thing is lowercased.

    The `sdlc-{env}-` prefix lets one vault hold several environments without their
    secrets colliding, and matches the convention the Postgres DSN names already use
    (`sdlc-dev-postgres-conn-string`). Override the whole prefix with KV_SECRET_PREFIX
    when a vault is dedicated to one environment and the repetition is just noise.
    """
    env = env or current_env()
    if prefix is None:
        prefix = os.environ.get("KV_SECRET_PREFIX")
    if prefix is None:
        prefix = f"sdlc-{env}-"
    return f"{prefix}{env_var.lower().replace('_', '-')}"


class SecretBootstrapError(RuntimeError):
    """The vault was required and could not be read. The process must not continue."""


def hydrate_environment() -> int:
    """Load platform secrets from Key Vault into os.environ. Returns how many were set.

    A no-op returning 0 in dev, which is what makes this safe to call unconditionally
    from `config/env.py`.

    KEY VAULT WINS over anything already in the environment. In a non-dev deployment the
    vault is the source of truth, and a stale `.env` on the host silently overriding it
    would defeat the entire point of putting the secrets in a vault.
    """
    if is_dev():
        logger.info("ENV=%s — secrets read from .env, Key Vault not contacted", current_env())
        return 0

    vault_url = (os.environ.get("AZURE_KEY_VAULT_URL") or "").strip()
    if not vault_url:
        raise SecretBootstrapError(
            f"ENV={current_env()} requires Azure Key Vault, but AZURE_KEY_VAULT_URL is not "
            f"set. Set it to https://<vault-name>.vault.azure.net, or set ENV=dev to read "
            f"secrets from .env instead."
        )

    try:
        # Imported here, not at module top. In dev this module is imported by config/env.py
        # on every process start including the test suite, and the azure SDK import costs
        # real time for something dev never uses.
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise SecretBootstrapError(
            f"ENV={current_env()} requires Azure Key Vault, but the azure SDK is not "
            f"installed: {exc}. Install azure-identity and azure-keyvault-secrets."
        ) from exc

    names = {var: secret_name_for(var) for var in PLATFORM_SECRETS}
    logger.info(
        "ENV=%s — loading %d secrets from %s", current_env(), len(names), vault_url
    )

    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
    except Exception as exc:
        raise SecretBootstrapError(
            f"Could not authenticate to Key Vault {vault_url}: {type(exc).__name__}: {exc}. "
            f"Locally this usually means `az login`; on Azure it means the managed identity "
            f"has no 'Key Vault Secrets User' role assignment."
        ) from exc

    def _fetch(item: tuple[str, str]) -> tuple[str, str | None, Exception | None]:
        env_var, secret_name = item
        try:
            return env_var, client.get_secret(secret_name).value, None
        except Exception as exc:  # ResourceNotFound and transport errors both land here
            return env_var, None, exc

    loaded = 0
    missing: list[str] = []
    hard_errors: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            for env_var, value, exc in pool.map(_fetch, names.items(), timeout=_TIMEOUT_SECONDS):
                if exc is not None:
                    # A 404 means "not configured", which is normal for any connector
                    # nobody has set up. Anything else is the vault or the network, and
                    # that is not something to shrug at.
                    if type(exc).__name__ == "ResourceNotFoundError":
                        missing.append(env_var)
                    else:
                        hard_errors.append(f"{env_var}: {type(exc).__name__}")
                    continue
                if value is not None:
                    os.environ[env_var] = value
                    loaded += 1
    except Exception as exc:
        raise SecretBootstrapError(
            f"Key Vault read failed against {vault_url}: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover - close is best effort
            pass

    if hard_errors:
        # Not "some secrets failed" — if the vault answered some reads and broke on
        # others, what loaded is arbitrary, and a process half-configured with
        # production credentials is worse than one that did not start.
        raise SecretBootstrapError(
            f"Key Vault {vault_url} failed on {len(hard_errors)} secret(s): "
            f"{', '.join(sorted(hard_errors))}. Refusing to start on a partial "
            f"configuration."
        )

    # NAMES ONLY, NEVER VALUES. This line exists so an operator can tell whether a
    # missing setting means "not in the vault" or "in the vault and not read"; printing
    # what was loaded would put every platform credential in the log.
    logger.info(
        "Key Vault hydration complete: %d loaded, %d not configured%s",
        loaded,
        len(missing),
        f" ({', '.join(sorted(missing))})" if missing else "",
    )
    return loaded
