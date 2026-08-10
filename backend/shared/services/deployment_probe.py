"""Live verify probes for deployment / CI-CD connectors.

These probes confirm a pasted credential is valid at connect time. They live
outside config.connectors because that factory is built for work-item board
providers; a CI/CD credential check does not fit that abstraction.

Each probe returns (ok, account, error) and never raises — a failed probe is a
verification result, not a 500.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_GITHUB_USER_URL = "https://api.github.com/user"


async def probe_github_actions(
    pat: str, owner: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Verify a GitHub PAT by calling the authenticated-user endpoint.

    Returns (ok, account, error). `account` is the provided owner/org or the
    token's own login. `error` carries an exception type name only — never the
    raw message — to avoid leaking token material into logs/responses.
    """
    if not pat:
        return False, None, "missing_pat"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _GITHUB_USER_URL,
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except Exception as exc:  # noqa: BLE001 — network/timeout → invalid, not 500
        return False, None, type(exc).__name__

    if resp.status_code != 200:
        return False, None, f"http_{resp.status_code}"

    login: Optional[str] = None
    try:
        login = resp.json().get("login")
    except Exception:  # noqa: BLE001
        login = None
    account = (owner or "").strip() or login
    return True, account, None
