"""Tests for the notification fan-out and the two seams that call it.

Context: SlackConnector.notify_slack existed, tested and audited, with ZERO production
call sites — gates opened and SLAs breached and nobody was told. notify_all is the
dispatch that was missing. Its contract is unusually strict because both callers are
best-effort seams that must never fail:

  - never raises
  - channels are independent (one failing must not suppress the other)
  - nothing is delivered unless the tenant explicitly configured a target
  - failures log a type name, never str(exc), which can carry a webhook URL or token
"""
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.services as _services  # noqa: E402

# Imported for its SIDE EFFECT, not its name: this is what sets the `secret_store`
# attribute on the `shared.services` package, and _patch_store patches that attribute.
# Without it the tests pass or fail on whether some EARLIER test in the run happened to
# import the submodule first — which is exactly the import-order dependence being fixed
# here, just pointing the other way.
import shared.services.secret_store  # noqa: E402,F401
import shared.services.notification_targets as targets  # noqa: E402
from shared.services.notify_dispatch import notify_all  # noqa: E402

_T = "tenant-1"


class _Conn:
    """Connector stub that records calls, or raises for a nominated kind."""

    def __init__(self, kind, sink, failing=None):
        self.kind, self.sink, self.failing = kind, sink, failing

    async def write_adapter(self, operation, **kwargs):
        if self.kind == self.failing:
            raise RuntimeError(f"{self.kind} is down")
        self.sink.append((self.kind, operation, kwargs))


def _factory(sink, failing=None):
    async def _get(kind=None, tenant_id=None):
        return _Conn(kind, sink, failing)

    return _get


def _target(value):
    async def _fn(tenant_id):
        return value

    return _fn


@pytest.mark.unit
async def test_no_configured_targets_delivers_nothing():
    """The default for every tenant today — silent, and deliberately so (REQ-M6-04)."""
    with patch.object(targets, "slack_target", _target(None)), patch.object(
        targets, "teams_target", _target(None)
    ):
        assert await notify_all(_T, "hello") == {}


@pytest.mark.unit
async def test_delivers_to_every_configured_channel():
    sink = []
    import config.connector_factory as cf

    with patch.object(targets, "slack_target", _target({"channel": "#eng"})), patch.object(
        targets, "teams_target", _target({"webhook_url": "https://hook"})
    ), patch.object(cf, "get_connector_for_session", _factory(sink)):
        result = await notify_all(_T, "gate waiting", title="Approval needed", link_url="https://a/r/1")

    assert result == {"slack": True, "ms_teams": True}
    kinds = {kind for kind, _, _ in sink}
    assert kinds == {"slack", "ms_teams"}


@pytest.mark.unit
async def test_one_failing_channel_does_not_suppress_the_other():
    """A Teams outage must not cost you the Slack notification."""
    sink = []
    import config.connector_factory as cf

    with patch.object(targets, "slack_target", _target({"channel": "#eng"})), patch.object(
        targets, "teams_target", _target({"webhook_url": "https://hook"})
    ), patch.object(cf, "get_connector_for_session", _factory(sink, failing="ms_teams")):
        result = await notify_all(_T, "gate waiting")

    assert result == {"slack": True, "ms_teams": False}
    assert [kind for kind, _, _ in sink] == ["slack"]


@pytest.mark.unit
async def test_never_raises_even_when_everything_is_broken():
    import config.connector_factory as cf

    async def _explode(**_kw):
        raise RuntimeError("catastrophe")

    with patch.object(targets, "slack_target", _target({"channel": "#eng"})), patch.object(
        targets, "teams_target", _target({"webhook_url": "https://hook"})
    ), patch.object(cf, "get_connector_for_session", _explode):
        assert await notify_all(_T, "hi") == {"slack": False, "ms_teams": False}


@pytest.mark.unit
async def test_failures_log_a_type_name_not_the_exception_text(caplog):
    """str(exc) on a delivery failure can echo the webhook URL back into the logs."""
    import config.connector_factory as cf

    async def _leaky(**_kw):
        raise RuntimeError("failed posting to https://outlook.office.com/webhook/SECRET")

    with caplog.at_level(logging.WARNING), patch.object(
        targets, "slack_target", _target(None)
    ), patch.object(targets, "teams_target", _target({"webhook_url": "https://hook"})), patch.object(
        cf, "get_connector_for_session", _leaky
    ):
        await notify_all(_T, "hi")

    assert "SECRET" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.unit
async def test_empty_inputs_are_ignored():
    assert await notify_all("", "hi") == {}
    assert await notify_all(_T, "") == {}


# ── Target resolution ─────────────────────────────────────────────────────────


class _Store:
    DISCONNECTED_MARKER = "__disconnected__"

    def __init__(self, data):
        self.data = data

    async def get_secret(self, tenant_id, ref):
        return self.data.get(ref)


def _patch_store(data):
    """Swap the secret store that notification_targets resolves at call time.

    PATCHES THE PACKAGE ATTRIBUTE, NOT sys.modules — and the difference is the whole
    reason these tests used to fail in a full run while passing on their own.

    `notification_targets` does a lazy `from shared.services import secret_store`
    inside the function. That statement imports the PACKAGE and then does a getattr on
    it. The submodule attribute is set on the package the first time anything imports
    `shared.services.secret_store` for real — so:

      run alone      nothing had imported it, getattr misses, the import machinery
                     falls through to sys.modules and finds the patched fake. Passes.
      run in a suite an earlier test imported it, getattr HITS the real module, and
                     the sys.modules entry is never consulted. The patch does nothing,
                     get_secret returns None, and the assertion fails.

    Patching the attribute is what the lookup actually reads, so it holds either way.
    That the old form worked at all was an accident of import order.
    """
    return patch.object(_services, "secret_store", _Store(data))


@pytest.mark.unit
async def test_teams_target_prefers_the_webhook_transport():
    """It needs no Entra app, so it is the one most likely to actually work."""
    with _patch_store(
        {"msteams-webhook-url": "https://hook", "msteams-team-id": "T", "msteams-channel-id": "C"}
    ):
        assert await targets.teams_target(_T) == {"webhook_url": "https://hook"}


@pytest.mark.unit
async def test_teams_target_needs_both_halves_of_a_graph_target():
    with _patch_store({"msteams-team-id": "T"}):
        assert await targets.teams_target(_T) is None


@pytest.mark.unit
async def test_a_disconnected_ref_reads_as_unconfigured():
    with _patch_store({"msteams-webhook-url": _Store.DISCONNECTED_MARKER}):
        assert await targets.teams_target(_T) is None


@pytest.mark.unit
async def test_slack_has_no_default_channel():
    """Slack goes live for the first time here — nothing fires until someone opts in."""
    with _patch_store({}):
        assert await targets.slack_target(_T) is None
    with _patch_store({"slack-channel": "#eng"}):
        assert await targets.slack_target(_T) == {"channel": "#eng"}


@pytest.mark.unit
async def test_sharepoint_target_requires_a_drive():
    with _patch_store({"sharepoint-site-id": "s1"}):
        assert await targets.sharepoint_target(_T) is None
    with _patch_store({"sharepoint-site-id": "s1", "sharepoint-drive-id": "d1"}):
        got = await targets.sharepoint_target(_T)
    assert got["drive_id"] == "d1"
    assert got["folder"] == targets.DEFAULT_SHAREPOINT_FOLDER


@pytest.mark.unit
async def test_target_resolution_never_raises():
    class _Broken:
        DISCONNECTED_MARKER = "__disconnected__"

        async def get_secret(self, *_a, **_kw):
            raise RuntimeError("vault down")

    with patch.dict(sys.modules, {"shared.services.secret_store": _Broken()}):
        assert await targets.teams_target(_T) is None
        assert await targets.slack_target(_T) is None
        assert await targets.sharepoint_target(_T) is None


# ── The gate seam ─────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_gate_notification_delivers_and_deep_links():
    from shared.services.orchestrator import gate_routing

    captured = {}

    async def _spy(tenant_id, message, *, title="", link_url=""):
        captured.update(
            tenant_id=tenant_id, message=message, title=title, link_url=link_url
        )
        return {"slack": True}

    with patch("shared.services.notify_dispatch.notify_all", _spy), patch(
        "shared.audit.service.audit_service.emit", _noop
    ):
        await gate_routing.notify_gate_pending("run-1", "deployment", "sre_lead", _T)

    assert "run-1" in captured["message"]
    assert "deployment" in captured["message"]
    # Deep link into the authenticated UI rather than an inline approve action.
    assert captured["link_url"].endswith("/runs/run-1")


@pytest.mark.unit
async def test_gate_notification_never_raises_into_the_gate_flow():
    from shared.services.orchestrator import gate_routing

    async def _explode(*_a, **_kw):
        raise RuntimeError("everything is on fire")

    with patch("shared.services.notify_dispatch.notify_all", _explode), patch(
        "shared.audit.service.audit_service.emit", _explode
    ):
        await gate_routing.notify_gate_pending("run-1", "design", "tech_lead", _T)


async def _noop(*_a, **_kw):
    return None
