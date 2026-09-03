"""No route is bound to a private helper.

THE BUG THIS CATCHES, found by pressing "Pull stories" in the product.

    @projects_router.post("/{project_id}/ingest-board", ...)
    def _board_item_url(connector, board, source_key, detail): ...   # <- a HELPER

    async def ingest_board(project_id, request, body, db): ...       # <- never routed

A decorator drifted one function too high. FastAPI registered the helper without
complaint and tried to resolve `connector: Any`, `board`, `source_key` and `detail`
from the HTTP request, so every pull returned 500 — for every board, every project,
every user. The board picker still worked, because listing boards is a different
route, which is exactly what made it look like a credential problem.

Nothing else would have caught it: the real handler is fully tested, and its tests
call the function directly rather than through the router.

A leading underscore is the signal. It says "not part of this module's surface", and
a route handler is the most public thing a module has.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.unit


def _routes():
    import process_api

    return [
        r for r in process_api.app.routes
        if hasattr(r, "endpoint") and hasattr(r, "path")
    ]


def test_there_are_routes_to_check():
    """A collector that finds nothing would make the assertions below vacuous."""
    assert len(_routes()) > 100


def test_no_route_is_handled_by_a_private_function():
    """THE INVARIANT."""
    private = sorted({
        f"{sorted(getattr(r, 'methods', {'?'}))} {r.path} -> {r.endpoint.__name__}"
        for r in _routes()
        if r.endpoint.__name__.startswith("_")
    })
    assert not private, (
        "These routes are handled by private helpers, which usually means a decorator "
        "sits above the wrong function:\n  " + "\n  ".join(private)
    )


def test_the_board_ingest_route_reaches_its_real_handler():
    """The specific regression. Named so a failure says what broke rather than
    just 'a private handler somewhere'."""
    ingest = [r for r in _routes() if r.path.endswith("/ingest-board")]
    assert ingest, "the board ingest route is not registered at all"
    assert ingest[0].endpoint.__name__ == "ingest_board", (
        f"/ingest-board is handled by {ingest[0].endpoint.__name__!r}"
    )


def test_the_board_ingest_handler_takes_a_request_not_a_connector():
    """What made the wrong binding obvious once seen: the helper's first parameter was
    a connector object, which no HTTP request can supply."""
    import inspect

    ingest = [r for r in _routes() if r.path.endswith("/ingest-board")][0]
    params = list(inspect.signature(ingest.endpoint).parameters)
    assert "request" in params
    assert "connector" not in params
