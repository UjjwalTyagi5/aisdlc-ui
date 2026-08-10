"""Shared process-wide Azure credential + Azure SDK logging configuration.

WHY: DefaultAzureCredential walks a chain (Environment -> Managed Identity/IMDS ->
SharedTokenCache -> Azure CLI). Locally the first three fail — Managed Identity in
particular times out probing 169.254.169.254 and logs a full traceback. Building a NEW
credential on every Key Vault / Blob call re-runs that whole chain each time (slow boot,
walls of tracebacks). Reusing ONE credential makes it probe once and cache both the
working inner credential and its token, so later reads go straight to the Azure CLI
(local) / Managed Identity (prod) with no re-probe.

Auth itself is unaffected — `az login` (local) or Managed Identity (prod) still provides
the token. This only removes redundant probing and noise.
"""
import logging

from azure.identity.aio import DefaultAzureCredential

from config.env import AGENT_RUNTIME_MODE

# Quiet the noisy Azure SDK loggers. The credential chain logs each failed attempt at
# INFO/DEBUG (with tracebacks) before the working one succeeds, and the HTTP policy logs
# every request/response. WARNING keeps genuine failures visible without the spam.
_LOGGER_LEVELS = {
    "azure.identity": logging.WARNING,
    "azure.identity.aio": logging.WARNING,
    "azure.core.pipeline.policies.http_logging_policy": logging.WARNING,
    # Chatty HTTP client loggers — the connector health probe + KV calls emit a lot.
    "httpcore": logging.WARNING,
    "httpx": logging.WARNING,
    "urllib3": logging.WARNING,
    # Our KV helper: hide per-secret DEBUG ("Loaded secret") and the expected
    # "not set" misses (downgraded to DEBUG in keyvault.py); real failures stay WARNING.
    "shared.keyvault": logging.INFO,
}
for _name, _level in _LOGGER_LEVELS.items():
    logging.getLogger(_name).setLevel(_level)

logger = logging.getLogger(__name__)

_credential = None


def get_azure_credential():
    """Return the process-wide async Azure credential (created lazily, reused).

    Do NOT close the returned credential — it is shared for the lifetime of the process.

    Local/dev EXCLUDES the Managed Identity credential from the chain. Its default
    probe hits IMDS (169.254.169.254); on networks that don't fast-reject that address —
    a phone hotspot, some VPNs — the probe HANGS ~12s, pushing every Key Vault read to
    the 15s timeout (measured: full chain ~13.6s vs. IMDS-excluded ~2s for the same
    secret, with the token cached in-process so repeated reads stay ~2s). The rest of
    the chain (Environment, SharedTokenCache, Azure CLI `az login`) is kept and still
    caches tokens. Prod/enterprise keeps the FULL chain so Managed Identity works on Azure.

    process_timeout=60 (default 10): a cold `az` on Windows can take >10s to start,
    which would trip the default and fail the read. 60s covers cold-start.
    """
    global _credential
    if _credential is None:
        if AGENT_RUNTIME_MODE in ("local", "dev"):
            logger.info("Azure credential: local/dev — DefaultAzureCredential (Managed Identity EXCLUDED)")
            _credential = DefaultAzureCredential(
                exclude_managed_identity_credential=True, process_timeout=60
            )
        else:
            logger.info("Azure credential: %s — full DefaultAzureCredential chain", AGENT_RUNTIME_MODE)
            _credential = DefaultAzureCredential(process_timeout=60)
    return _credential
