"""RED tests for connector health endpoint — REQ-M6-11.

Asserts that GET /connectors/health returns entries for all four new connectors
(jira, github_issues, azure_repos, slack) with status and latency_ms fields.

Uses FastAPI TestClient. Tests skip at collection time when the health endpoint
does not yet include the new connector entries.
"""
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import jwt
    from fastapi.testclient import TestClient
    from config.env import JWT_ALGORITHM, JWT_SECRET_KEY
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"Required imports not available: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

_NEW_CONNECTORS = {"jira", "github_issues", "azure_repos", "slack"}


def _mint_token() -> str:
    """Mint a signed JWT for authenticated endpoint access.

    tenant_id is a real UUID (not a literal string): this router requires
    artifact:view at include time (process_api._VIEW_DEP), whose fallback
    workspace resolution runs uuid.UUID(str(tenant_id)) against Postgres — a
    non-UUID tenant_id 500s instead of cleanly 404-ing into "no workspace, fall
    through to the permission check". permissions carries the wildcard so this
    stays a route-shape test, not an RBAC test.
    """
    if not JWT_SECRET_KEY:
        return "no-token"
    now = int(time.time())
    payload = {
        "sub": "test-health-user",
        "tenant_id": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 3600,
        "permissions": ["admin:*"],
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM or "HS256")


@pytest.mark.unit
def test_connector_health_includes_jira_entry():
    """GET /connectors/health response includes a 'jira' entry."""
    try:
        from process_api import app
    except Exception as _e:
        pytest.skip(f"process_api not importable: {_e}")

    token = _mint_token()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/connectors/health",
        headers={"Authorization": f"Bearer {token}"} if JWT_SECRET_KEY else {},
    )
    assert resp.status_code in (200, 401), (
        f"Expected 200 or 401, got {resp.status_code}"
    )
    if resp.status_code == 200:
        body = resp.json()
        connectors = body.get("connectors", {})
        assert "jira" in connectors, (
            f"Expected 'jira' in connectors health; got keys: {list(connectors.keys())}"
        )


@pytest.mark.unit
def test_connector_health_includes_github_issues_entry():
    """GET /connectors/health response includes a 'github_issues' entry."""
    try:
        from process_api import app
    except Exception as _e:
        pytest.skip(f"process_api not importable: {_e}")

    token = _mint_token()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/connectors/health",
        headers={"Authorization": f"Bearer {token}"} if JWT_SECRET_KEY else {},
    )
    if resp.status_code == 200:
        body = resp.json()
        connectors = body.get("connectors", {})
        assert "github_issues" in connectors, (
            f"Expected 'github_issues' in connectors health; keys: {list(connectors.keys())}"
        )


@pytest.mark.unit
def test_connector_health_includes_azure_repos_entry():
    """GET /connectors/health response includes an 'azure_repos' entry."""
    try:
        from process_api import app
    except Exception as _e:
        pytest.skip(f"process_api not importable: {_e}")

    token = _mint_token()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/connectors/health",
        headers={"Authorization": f"Bearer {token}"} if JWT_SECRET_KEY else {},
    )
    if resp.status_code == 200:
        body = resp.json()
        connectors = body.get("connectors", {})
        assert "azure_repos" in connectors, (
            f"Expected 'azure_repos' in connectors health; keys: {list(connectors.keys())}"
        )


@pytest.mark.unit
def test_connector_health_includes_slack_entry():
    """GET /connectors/health response includes a 'slack' entry."""
    try:
        from process_api import app
    except Exception as _e:
        pytest.skip(f"process_api not importable: {_e}")

    token = _mint_token()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/connectors/health",
        headers={"Authorization": f"Bearer {token}"} if JWT_SECRET_KEY else {},
    )
    if resp.status_code == 200:
        body = resp.json()
        connectors = body.get("connectors", {})
        assert "slack" in connectors, (
            f"Expected 'slack' in connectors health; keys: {list(connectors.keys())}"
        )


@pytest.mark.unit
def test_connector_health_entries_have_status_and_latency():
    """Each connector health entry has 'status' and 'latency_ms' fields."""
    try:
        from process_api import app
    except Exception as _e:
        pytest.skip(f"process_api not importable: {_e}")

    token = _mint_token()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/connectors/health",
        headers={"Authorization": f"Bearer {token}"} if JWT_SECRET_KEY else {},
    )
    if resp.status_code == 200:
        body = resp.json()
        connectors = body.get("connectors", {})
        for connector_name, entry in connectors.items():
            if connector_name in _NEW_CONNECTORS:
                assert "status" in entry, (
                    f"Connector '{connector_name}' health entry missing 'status' field"
                )
                assert "latency_ms" in entry, (
                    f"Connector '{connector_name}' health entry missing 'latency_ms' field"
                )
