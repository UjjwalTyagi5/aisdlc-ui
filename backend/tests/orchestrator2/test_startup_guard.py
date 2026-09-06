"""The capability-registry boot guard must actually RUN at startup.

This file used to assert `"validate_registry" in inspect.getsource(process_api)`.
`process_api` imports `validate_registry` at line 70, so the IMPORT LINE ALONE
satisfied that assertion: deleting the call from `lifespan` and leaving the import in
place kept the suite green. The guard against "six of nine agents ran with no system
prompt without anyone noticing" — which is the entire reason `registry.py` exists —
was protected by a test that could not fail.

So the two tests below ask the two questions a string search cannot:

  1. Does the call RUN? `validate_registry` is replaced with something that raises,
     the real `lifespan` is entered, and the sentinel must come back out. That proves
     the call is reached AND that a registry gap is FATAL rather than logged — the
     failure mode the old engine had, and the one this guard is a response to.
  2. Is it UNCONDITIONAL, and after the RBAC catalogue guard? A call moved behind a
     condition that happens to be false under test would pass (1) while shipping a
     process that boots with a broken registry. That question is structural, so it is
     answered structurally, over the AST of `lifespan` — not over its source text,
     which is where the original mistake lived.

Test (1) stubs everything the lifespan touches BEFORE the guard. If someone adds a new
startup step ahead of it, this test fails with a different exception than the sentinel
and says so — extend the stub list rather than weakening the assertion.
"""
import ast
import inspect
import textwrap
from contextlib import asynccontextmanager

import pytest


class _RegistryGap(RuntimeError):
    """Stands in for whatever `validate_registry` raises on a real registry gap."""


def _lifespan_ast():
    """`lifespan`'s body as an AST. `@asynccontextmanager` sets `__wrapped__`, so
    `getsource` reaches the real function."""
    import process_api

    tree = ast.parse(textwrap.dedent(inspect.getsource(process_api.lifespan)))
    fn = tree.body[0]
    assert isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)), fn
    return fn


def _top_level_call_names(fn):
    """Names called as bare statements DIRECTLY in the function body — i.e. on every
    boot, not inside an `if`, a `try`, a loop, or a `with`. `await f()` counts."""
    names = []
    for index, node in enumerate(fn.body):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            names.append((index, value.func.id))
    return names


def test_startup_guard_is_unconditional_and_follows_the_rbac_catalogue_guard():
    """Structural, over the AST rather than the source text. `assert_rbac_catalog` is
    reached inside an `async with` block, so it is located by walking; the registry
    guard must be a bare statement in the lifespan body itself, after it."""
    fn = _lifespan_ast()

    calls = _top_level_call_names(fn)
    positions = [i for i, name in calls if name == "validate_registry"]
    assert positions, (
        "validate_registry() is not called unconditionally in lifespan's body. An "
        "import of the name is not a call, and a call behind an `if` is not a guard — "
        "a registry gap must stop EVERY boot, exactly as RBAC catalogue drift does."
    )

    rbac_at = None
    for index, node in enumerate(fn.body):
        if any(
            isinstance(inner, ast.Name) and inner.id == "assert_rbac_catalog"
            for inner in ast.walk(node)
        ):
            rbac_at = index
            break
    assert rbac_at is not None, "the RBAC catalogue guard is gone from lifespan"
    assert positions[0] > rbac_at, (
        "the registry guard must follow the RBAC catalogue guard — the catalogue has "
        "to be correct before the first role binding is written"
    )


@pytest.mark.asyncio
async def test_a_registry_gap_stops_the_process(monkeypatch):
    """The behavioural half: enter the REAL lifespan and prove the guard's failure
    reaches the caller. A gap must be fatal, not a warning — the old engine logged one
    and served vague answers nobody could chase."""
    import process_api

    called = []

    def _boom():
        called.append(True)
        raise _RegistryGap("capability registry is missing an agent")

    monkeypatch.setattr(process_api, "validate_registry", _boom)

    # Everything the lifespan does before the guard, stubbed. Each of these is a
    # network or database call; none of them is what this test is about.
    async def _no_secret(name):
        return None

    async def _probe_ok(*args, **kwargs):
        return "ok"

    @asynccontextmanager
    async def _catalog_session():
        yield object()

    async def _catalog_ok(session, autorepair=False):
        return None

    monkeypatch.setattr(process_api, "load_secret", _no_secret)
    monkeypatch.setattr(process_api, "AZURE_BLOB_ACCOUNT_URL", "")
    monkeypatch.setattr(process_api, "_probe_postgres", _probe_ok)
    monkeypatch.setattr(process_api, "_probe_redis", _probe_ok)
    monkeypatch.setattr(process_api, "_probe_blob", _probe_ok)
    monkeypatch.setattr(process_api, "ENABLE_LITELLM", False)
    monkeypatch.setattr(process_api, "ENABLE_CONNECTOR_HEALTH_PROBES", False)
    monkeypatch.setattr(process_api, "ENABLE_WORKER_POOL", False)
    monkeypatch.setattr(process_api, "ENABLE_SCIM", False)
    monkeypatch.setattr(process_api, "REDIS_URL", "")
    monkeypatch.setattr(process_api, "AGENT_RUNTIME_MODE", "local")
    monkeypatch.setattr(process_api, "_check_oidc_audience_guard", lambda: None)
    monkeypatch.setattr(process_api, "get_db_session_superuser", _catalog_session)
    monkeypatch.setattr(process_api, "assert_rbac_catalog", _catalog_ok)

    from fastapi import FastAPI

    with pytest.raises(BaseException) as caught:
        async with process_api.lifespan(FastAPI()):
            pass

    assert isinstance(caught.value, _RegistryGap), (
        f"the lifespan raised {caught.value!r} instead of reaching the registry "
        f"guard. If a new startup step was added before it, stub that step here — do "
        f"not weaken this assertion. validate_registry called: {bool(called)}"
    )
    assert called, "validate_registry() was never called at startup"
