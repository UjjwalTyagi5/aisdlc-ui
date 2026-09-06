"""Every WebSocket route has made a conscious authz decision.

`assert_all_routes_protected` skipped anything that was not an `APIRoute` — the loop's
own comment says so — so every socket in the app was invisible to it. That is how
`/projects/[id]/orchestrator` shipped in Phase 1 with no access check at all: three of
four surfaces were gated, the fourth was not, and no sweep covered it.

WebSocket routes cannot carry `require_permission`: there is no request/response cycle
to hang a dependency on. They authenticate INSIDE the handler, redeeming a single-use
ticket and resolving the caller's role before accepting the connection. So the
guarantee here is weaker by necessity and still worth having: every socket is NAMED,
and a new one fails the boot until somebody records which it is.

WHAT THIS IS NOT. Being in the allowlist is not an audit. It records the sockets that
existed when the scan was extended; confirming that each one's in-handler check is real
is tracked as carried debt. Claiming otherwise would be the ninth instance of prose
asserting a guarantee the code does not provide.
"""
import pytest

from shared.authz import dependency as dep


def test_websocket_routes_are_enumerated_not_skipped():
    import process_api

    ws_paths = dep.websocket_route_paths(process_api.app)
    assert ws_paths, "the sweep found no WebSocket routes at all — it is still blind"
    assert any("orchestrator2" in p for p in ws_paths), sorted(ws_paths)


def test_every_websocket_route_is_recorded():
    import process_api

    unrecorded = dep.unrecorded_websocket_routes(process_api.app)
    assert unrecorded == [], (
        "these sockets have no recorded authz decision — add each to "
        "_WS_IN_HANDLER_AUTH_PATHS once you have checked what it actually does: "
        + ", ".join(unrecorded)
    )


def test_an_unrecorded_socket_is_reported():
    """The guard on the guard. Without this, `unrecorded_websocket_routes` could return
    [] unconditionally and every other test in this file would still pass."""
    class _Route:
        path = "/sdlc/agent/brand-new/ws"

    assert dep._ws_route_is_recorded(_Route()) is False


def test_the_retired_engines_are_not_in_the_allowlist():
    """Phase 5 deleted them. A lingering entry would silently re-bless the path if one
    were ever re-added under the same name."""
    for gone in ("/sdlc/agent/copilot/ws", "/sdlc/agent/orchestrator/ws"):
        assert gone not in dep._WS_IN_HANDLER_AUTH_PATHS, gone


def test_the_boot_scan_fails_on_an_unrecorded_socket(monkeypatch):
    """End to end through the real `assert_all_routes_protected`, because that is what
    actually runs at boot — the helpers above could be correct while the scan never
    consults them."""
    import process_api

    monkeypatch.setattr(dep, "_WS_IN_HANDLER_AUTH_PATHS", set())
    with pytest.raises(RuntimeError) as caught:
        dep.assert_all_routes_protected(process_api.app)
    assert "websocket" in str(caught.value).lower()


def test_the_boot_scan_passes_as_configured():
    """And the other half: with the allowlist as shipped, the app boots."""
    import process_api

    dep.assert_all_routes_protected(process_api.app)
