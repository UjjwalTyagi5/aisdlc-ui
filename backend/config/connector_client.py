"""Returns connector credentials from environment variables.

Agents call get_connector("azure_devops") instead of reading env constants
directly.  Django is no longer a dependency — credentials are resolved from
env vars only so the FastAPI service operates independently.
"""
from config.env import ADO_ORG_URL, ADO_PAT


class ConnectorNotInstalledError(Exception):
    pass


def get_connector(kind: str) -> dict:
    """Return {"org_url": ..., "pat": ..., "health": ...} for the given connector kind.

    Resolution order:
    1. Env vars ADO_ORG_URL / ADO_PAT for azure_devops
    2. Raises ConnectorNotInstalledError with a user-friendly message
    """
    if kind == "azure_devops" and ADO_ORG_URL and ADO_PAT:
        return {
            "kind": "azure_devops",
            "display_name": "Azure DevOps",
            "org_url": ADO_ORG_URL,
            "pat": ADO_PAT,
            "health": "unknown",
        }

    raise ConnectorNotInstalledError(
        f"The '{kind}' connector is not configured. "
        "Set ADO_ORG_URL and ADO_PAT environment variables."
    )
