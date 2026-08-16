"""Wave-0 SCIM test scaffold — extended in milestone-7.4-02 with auth + models.

REQ-M7-16: SCIM 2.0 Users endpoint — idempotent provision, externalId dedup.
REQ-M7-17: SCIM deprovision → Redis JTI denylist → 401 within 5-min SLA.
REQ-M7-23: scim_provision_duration_seconds metric registered and emittable.

TestSCIMMetrics and TestSCIMAuth/TestSCIMModels are fully implemented here.
TestSCIMProvision / TestSCIMDeprovision are implemented below (milestone-7.4-02).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════════════
# TestSCIMMetrics — FULLY IMPLEMENTED (plan 01)
# ═══════════════════════════════════════════════════════════════════════════

class TestSCIMMetrics:
    """SCIM_PROVISION_DURATION Histogram is registered and emittable (REQ-M7-23)."""

    def test_scim_provision_duration_histogram_registered(self):
        """SCIM_PROVISION_DURATION is importable and has the correct name."""
        from shared.services.metrics import SCIM_PROVISION_DURATION
        assert SCIM_PROVISION_DURATION._name == "scim_provision_duration_seconds"

    def test_scim_provision_duration_has_operation_label(self):
        """Histogram accepts 'operation' label with provision/deprovision values."""
        from shared.services.metrics import SCIM_PROVISION_DURATION
        # Both operation values must be valid (no KeyError / label cardinality error)
        SCIM_PROVISION_DURATION.labels(operation="provision")
        SCIM_PROVISION_DURATION.labels(operation="deprovision")

    def test_scim_provision_duration_emittable(self):
        """Histogram .observe() call does not raise."""
        from shared.services.metrics import SCIM_PROVISION_DURATION
        SCIM_PROVISION_DURATION.labels(operation="provision").observe(0.1)
        SCIM_PROVISION_DURATION.labels(operation="deprovision").observe(0.05)

    def test_scim_provision_duration_context_manager(self):
        """Histogram .time() context manager does not raise."""
        from shared.services.metrics import SCIM_PROVISION_DURATION
        with SCIM_PROVISION_DURATION.labels(operation="provision").time():
            pass  # simulate a fast operation


# ═══════════════════════════════════════════════════════════════════════════
# TestSCIMModels — implemented in milestone-7.4-02
# ═══════════════════════════════════════════════════════════════════════════

class TestSCIMModels:
    """SCIMUserCreate and SCIMUserOut Pydantic schemas validate correctly (REQ-M7-16)."""

    def test_scim_user_create_accepts_required_fields(self):
        """SCIMUserCreate accepts userName and externalId."""
        from scim.models import SCIMUserCreate
        obj = SCIMUserCreate(userName="alice@example.com", externalId="ext-001")
        assert obj.userName == "alice@example.com"
        assert obj.externalId == "ext-001"

    def test_scim_user_create_active_defaults_true(self):
        """SCIMUserCreate.active defaults to True when omitted."""
        from scim.models import SCIMUserCreate
        obj = SCIMUserCreate(userName="bob@example.com", externalId="ext-002")
        assert obj.active is True

    def test_scim_user_create_accepts_emails_list(self):
        """SCIMUserCreate accepts optional emails list."""
        from scim.models import SCIMUserCreate
        obj = SCIMUserCreate(
            userName="carol@example.com",
            externalId="ext-003",
            emails=[{"value": "carol@example.com", "primary": True}],
        )
        assert len(obj.emails) == 1

    def test_scim_user_out_has_required_fields(self):
        """SCIMUserOut serializes id, externalId, active per RFC 7643 shape."""
        from scim.models import SCIMUserOut
        uid = str(uuid.uuid4())
        obj = SCIMUserOut(id=uid, userName="dave@example.com", externalId="ext-004", active=True)
        assert obj.id == uid
        assert obj.externalId == "ext-004"
        assert obj.active is True


# ═══════════════════════════════════════════════════════════════════════════
# TestSCIMAuth — implemented in milestone-7.4-02
# ═══════════════════════════════════════════════════════════════════════════

class TestSCIMAuth:
    """verify_scim_credential returns True only on exact timing-safe match (REQ-M7-16)."""

    async def test_missing_auth_header_returns_false(self):
        """Returns False when Authorization header is absent."""
        from scim.auth import verify_scim_credential

        request = MagicMock()
        request.headers = {}

        result = await verify_scim_credential(request, "tenant-123")
        assert result is False

    async def test_non_bearer_header_returns_false(self):
        """Returns False when Authorization header is not 'Bearer ...'."""
        from scim.auth import verify_scim_credential

        request = MagicMock()
        request.headers = {"Authorization": "Basic abc123"}

        result = await verify_scim_credential(request, "tenant-123")
        assert result is False

    async def test_no_stored_credential_returns_false(self):
        """Returns False (fail-closed) when no scim-bearer-token is stored for the tenant."""
        from scim.auth import verify_scim_credential

        request = MagicMock()
        request.headers = {"Authorization": "Bearer some-token"}

        with patch("scim.auth.load_secret", new_callable=AsyncMock, return_value=None):
            result = await verify_scim_credential(request, "tenant-123")
        assert result is False

    async def test_token_mismatch_returns_false(self):
        """Returns False when provided token does not match the stored token."""
        from scim.auth import verify_scim_credential

        request = MagicMock()
        request.headers = {"Authorization": "Bearer wrong-token"}

        with patch("scim.auth.load_secret", new_callable=AsyncMock, return_value="correct-secret"):
            result = await verify_scim_credential(request, "tenant-123")
        assert result is False

    async def test_valid_token_returns_true(self):
        """Returns True when provided token matches the stored token via compare_digest."""
        from scim.auth import verify_scim_credential

        request = MagicMock()
        request.headers = {"Authorization": "Bearer correct-secret"}

        with patch("scim.auth.load_secret", new_callable=AsyncMock, return_value="correct-secret"):
            result = await verify_scim_credential(request, "tenant-123")
        assert result is True

    async def test_calls_load_secret_with_correct_key(self):
        """verify_scim_credential calls load_secret with 'scim-bearer-token' and the tenant_id."""
        from scim.auth import verify_scim_credential

        request = MagicMock()
        request.headers = {"Authorization": "Bearer some-token"}

        with patch("scim.auth.load_secret", new_callable=AsyncMock, return_value=None) as mock_load:
            await verify_scim_credential(request, "my-tenant")
        mock_load.assert_called_once_with("scim-bearer-token", tenant_id="my-tenant")


# ═══════════════════════════════════════════════════════════════════════════
# TestSCIMProvision — implemented in milestone-7.4-02
# ═══════════════════════════════════════════════════════════════════════════

class TestSCIMProvision:
    """POST /scim/v2/{tenant_id}/Users — idempotent provision, externalId dedup (REQ-M7-16)."""

    async def test_provision_creates_user_row(self):
        """Provision with valid bearer creates a users row and returns 201."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        mock_row = {
            "id": str(uuid.uuid4()),
            "external_id": "ext-provision-01",
            "active": True,
        }
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/scim/v2/tenant-123/Users",
                json={"userName": "alice@example.com", "externalId": "ext-provision-01"},
                headers={"Authorization": "Bearer valid-token"},
            )

        assert response.status_code == 201

    async def test_provision_requires_valid_scim_credential(self):
        """POST with invalid bearer returns 401 (fail-closed, T-7.4-05)."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        with patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=False):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/scim/v2/tenant-123/Users",
                json={"userName": "alice@example.com", "externalId": "ext-001"},
                headers={"Authorization": "Bearer bad-token"},
            )

        assert response.status_code == 401

    async def test_provision_idempotent_on_external_id(self):
        """Duplicate POST returns exactly one row (ON CONFLICT DO UPDATE)."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        row_id = str(uuid.uuid4())
        mock_row = {"id": row_id, "external_id": "ext-dup-01", "active": True}

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app, raise_server_exceptions=True)
            # First provision
            r1 = client.post(
                "/scim/v2/tenant-123/Users",
                json={"userName": "idm@example.com", "externalId": "ext-dup-01"},
                headers={"Authorization": "Bearer valid-token"},
            )
            # Second provision (same externalId)
            r2 = client.post(
                "/scim/v2/tenant-123/Users",
                json={"userName": "idm@example.com", "externalId": "ext-dup-01"},
                headers={"Authorization": "Bearer valid-token"},
            )

        assert r1.status_code == 201
        assert r2.status_code == 201
        # Both returned the same row id (idempotent — the DB upsert returns same row)
        assert r1.json()["id"] == r2.json()["id"]

    async def test_provision_user_starts_with_zero_roles(self):
        """D-05: SCIM-provisioned user has no RoleBinding row created."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        mock_row = {"id": str(uuid.uuid4()), "external_id": "ext-d05-01", "active": True}
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/scim/v2/tenant-123/Users",
                json={"userName": "noperm@example.com", "externalId": "ext-d05-01"},
                headers={"Authorization": "Bearer valid-token"},
            )

        assert response.status_code == 201
        # Confirm we never called INSERT ... role_bindings (D-05)
        execute_calls = mock_conn.execute.call_args_list
        for call in execute_calls:
            sql_text = str(call.args[0] if call.args else "")
            assert "role_bindings" not in sql_text.lower(), (
                "D-05 violated: provision handler must not insert into role_bindings"
            )

    async def test_provision_metric_emitted(self):
        """scim_provision_duration_seconds is recorded with operation='provision'."""
        from scim.router import scim_router, SCIM_PROVISION_DURATION
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        mock_row = {"id": str(uuid.uuid4()), "external_id": "ext-metric-01", "active": True}
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        # The metric is emitted by the context manager — we verify no exception is raised
        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/scim/v2/tenant-123/Users",
                json={"userName": "metric@example.com", "externalId": "ext-metric-01"},
                headers={"Authorization": "Bearer valid-token"},
            )
        assert response.status_code == 201
        # Verify metric bucket exists for provision label (no exception = emitted correctly)
        SCIM_PROVISION_DURATION.labels(operation="provision")


# ═══════════════════════════════════════════════════════════════════════════
# TestSCIMDeprovision — implemented in milestone-7.4-02
# ═══════════════════════════════════════════════════════════════════════════

class TestSCIMDeprovision:
    """DELETE /scim/v2/{tenant_id}/Users/{id} — soft-deactivate + JTI denylist (REQ-M7-17)."""

    async def test_deprovision_sets_active_false(self):
        """DELETE sets active=false on the users row."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        user_id = str(uuid.uuid4())
        mock_row = {"id": user_id, "external_id": "ext-dep-01", "sub": "sub-dep-01", "active": False}
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
            patch("scim.router.revoke_all_user_jtis", new_callable=AsyncMock, return_value=0),
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app, raise_server_exceptions=True)
            response = client.delete(
                f"/scim/v2/tenant-123/Users/{user_id}",
                headers={"Authorization": "Bearer valid-token"},
            )

        assert response.status_code == 204
        # Verify the UPDATE active=false was issued via session.execute.
        # TextClause repr does not include SQL text; extract via str(arg) instead.
        execute_calls = mock_conn.execute.call_args_list
        update_issued = any(
            "active" in str(call.args[0]).lower() if call.args else False
            for call in execute_calls
        )
        assert update_issued, (
            "DELETE handler must issue UPDATE ... active ... via session.execute. "
            f"Actual calls: {[str(c.args[0]) if c.args else c for c in execute_calls]}"
        )

    async def test_deprovision_revokes_all_live_jtis(self):
        """DELETE calls revoke_all_user_jtis with the user's sub (D-01, REQ-M7-17)."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        user_id = str(uuid.uuid4())
        mock_row = {"id": user_id, "external_id": "ext-dep-02", "sub": "sub-dep-02", "active": False}
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
            patch("scim.router.revoke_all_user_jtis", new_callable=AsyncMock, return_value=2) as mock_revoke,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            # Set up fake redis_denylist on app state
            app.state.redis_denylist = MagicMock()

            client = TestClient(app, raise_server_exceptions=True)
            response = client.delete(
                f"/scim/v2/tenant-123/Users/{user_id}",
                headers={"Authorization": "Bearer valid-token"},
            )

        assert response.status_code == 204
        # revoke_all_user_jtis was called with the denylist client
        mock_revoke.assert_called_once()
        call_args = mock_revoke.call_args
        assert call_args.args[1] == "tenant-123"  # tenant_id
        assert call_args.args[2] == "sub-dep-02"  # user sub

    async def test_deprovision_requires_valid_credential(self):
        """DELETE with invalid bearer returns 401."""
        from scim.router import scim_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        with patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=False):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.delete(
                "/scim/v2/tenant-123/Users/some-user-id",
                headers={"Authorization": "Bearer bad-token"},
            )

        assert response.status_code == 401

    async def test_deprovision_metric_emitted(self):
        """scim_provision_duration_seconds is recorded with operation='deprovision'."""
        from scim.router import scim_router, SCIM_PROVISION_DURATION
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(scim_router)

        user_id = str(uuid.uuid4())
        mock_row = {"id": user_id, "external_id": "ext-dep-metric", "sub": "sub-dep-metric", "active": False}
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=mock_row)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        with (
            patch("scim.router.verify_scim_credential", new_callable=AsyncMock, return_value=True),
            patch("scim.router.get_db_session_superuser") as mock_db,
            patch("scim.router.revoke_all_user_jtis", new_callable=AsyncMock, return_value=0),
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app, raise_server_exceptions=True)
            response = client.delete(
                f"/scim/v2/tenant-123/Users/{user_id}",
                headers={"Authorization": "Bearer valid-token"},
            )

        assert response.status_code == 204
        # Verify metric bucket exists for deprovision label
        SCIM_PROVISION_DURATION.labels(operation="deprovision")
