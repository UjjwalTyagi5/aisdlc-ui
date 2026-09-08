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


# ── the audit the allowlist deliberately was not ─────────────────────────────
#
# Phase 5 extended the scan to LIST sockets and require each to be recorded, and said
# plainly that being listed was not an audit. This is the audit: every recorded socket
# must actually redeem a single-use ticket BEFORE it accepts the connection.
#
# It found four that did not. `/test-ws` endpoints in the requirements and ingestion
# agents called `websocket.accept()` immediately and echoed whatever they were sent —
# unauthenticated sockets mounted in the running app, invisible to every check until
# the sweep listed them. Nothing referenced them; they were debug scaffolding, and they
# are gone.


def _ws_handlers(app):
    """(path, handler) for every WebSocket route on the app."""
    out = []
    for route in app.routes:
        if route.__class__.__name__ in ("APIWebSocketRoute", "WebSocketRoute"):
            path = getattr(route, "path", "")
            fn = getattr(route, "endpoint", None)
            if path and fn is not None:
                out.append((path, fn))
    return out


def test_every_socket_authenticates_before_it_accepts():
    """A socket that accepts first and checks later has already let the caller in.

    Read off the source with docstrings stripped: several of these handlers DESCRIBE
    their ticket flow in prose, and a check that a docstring can satisfy is the defect
    this branch has corrected nine times.
    """
    import ast
    import inspect
    import textwrap

    import process_api

    offenders = []
    for path, fn in _ws_handlers(process_api.app):
        try:
            src = textwrap.dedent(inspect.getsource(fn))
        except (OSError, TypeError):  # pragma: no cover - not introspectable
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body.pop(0)
        code = ast.unparse(tree)

        if "_redeem_ws_ticket" not in code:
            offenders.append(f"{path}: never redeems a ticket")
            continue
        # And it must happen BEFORE the handshake is accepted.
        if "accept()" in code and code.index("_redeem_ws_ticket") > code.index("accept()"):
            offenders.append(f"{path}: accepts before redeeming")

    assert offenders == [], (
        "these sockets do not authenticate before accepting:\n  " + "\n  ".join(offenders)
    )


def test_no_unauthenticated_debug_socket_is_mounted():
    """The four that failed the audit. Named so re-adding one is a deliberate act."""
    import process_api

    paths = dep.websocket_route_paths(process_api.app)
    assert [p for p in paths if p.endswith("/test-ws")] == []
