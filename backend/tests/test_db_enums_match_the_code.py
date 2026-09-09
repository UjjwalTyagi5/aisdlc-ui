"""The database's CHECK constraints agree with the code's catalogues.

THE BUG THIS CATCHES, twice in one afternoon. A value list lives in Python AND in a
CHECK constraint, and adding a value in one place is silently valid until a user
triggers a write:

  `notifications.kind`         a new `artifact_superseded` was refused
  `governance_requests.type`   a new `artifact_consumption` was refused

Both times the code was entirely correct — the type registered, the approver resolved,
the payload assembled — and the INSERT was refused by a constraint nobody had updated.

WORSE THAN A CLEAN FAILURE, because a rejected statement ABORTS THE WHOLE POSTGRES
TRANSACTION. The visible symptom was not "bad value": it was an unrelated RLS error on
a different table and a string of 404s, several frames away from the cause.

`test_governance_requests.py` compares the Python maps to EACH OTHER — labels against
types, approvers against types. That is a real check and it passed throughout, because
the copy it never looked at was the one in the database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _allowed_values(constraint: str) -> set[str]:
    """The literals a CHECK constraint permits, parsed from its definition.

    Parsed rather than hardcoded: a copy here would be a THIRD list to keep in step,
    which is the problem this file exists to solve.
    """
    import re

    async with get_db_session_superuser() as db:
        definition = (await db.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :c"),
            {"c": constraint},
        )).scalar()

    assert definition, f"constraint {constraint!r} does not exist"
    values = set(re.findall(r"'([a-z0-9_]+)'::character varying", definition))
    if not values:
        values = set(re.findall(r"'([a-z0-9_]+)'", definition))
    assert values, f"parsed no values from {constraint!r}: {definition}"
    return values


async def test_governance_request_types_match_the_database():
    """`REQUEST_TYPES` is the catalogue; the constraint is what actually accepts a row.
    A type in the code but not the constraint fails only when somebody files one."""
    from shared.governance.routing import REQUEST_TYPES

    allowed = await _allowed_values("ck_governance_request_type")
    assert set(REQUEST_TYPES) == allowed, (
        "governance_requests.type CHECK and routing.REQUEST_TYPES disagree.\n"
        f"  in code only: {sorted(set(REQUEST_TYPES) - allowed)}\n"
        f"  in DB only:   {sorted(allowed - set(REQUEST_TYPES))}\n"
        "Adding a request type needs a migration as well as a Python change."
    )


async def test_project_delivery_statuses_match_the_database():
    """A THIRD copy of this list lives in frontend/lib/schemas/project.ts. The picker
    offering a value the database refuses is how this field shipped broken the first
    time — see 0054 — so the two copies this suite can reach are compared here."""
    from shared.routers.projects import _DELIVERY_STATUSES

    allowed = await _allowed_values("ck_project_delivery_status")
    assert set(_DELIVERY_STATUSES) == allowed, (
        "projects.delivery_status CHECK and _DELIVERY_STATUSES disagree. "
        f"in code only: {sorted(set(_DELIVERY_STATUSES) - allowed)}; "
        f"in DB only: {sorted(allowed - set(_DELIVERY_STATUSES))}"
    )


async def test_notification_kinds_the_code_emits_are_all_accepted():
    """Every `kind` any caller passes to `emit` must be one the constraint allows.

    Asserted in one direction only, deliberately: the constraint may legitimately
    permit kinds nothing emits yet (a UI shipped ahead of its producer), but a kind
    the code emits and the database refuses is always a bug.
    """
    import ast

    allowed = await _allowed_values("ck_notification_kind")
    root = Path(__file__).resolve().parents[1]

    # AST, not a regex over the text. `kind="..."` is a keyword argument on plenty of
    # unrelated calls in this codebase — connector kinds, artifact kinds — and a
    # regex swept all of them up, reporting 23 false positives. Only the `kind=` of a
    # call named `emit` is a notification kind.
    emitted: set[str] = set()
    # Cloned working copies live inside this tree (the target dialogs clone into
    # DEV_WORKSPACE_ROOT) and are not our source: a checkout of this codebase would
    # otherwise be scanned twice, and somebody else's `emit(kind=...)` would be read
    # as ours. Pruned by resolved path so moving the workspace root moves this too.
    from config.env import DEV_WORKSPACE_ROOT  # noqa: PLC0415

    workspace = Path(DEV_WORKSPACE_ROOT).resolve()
    for path in root.rglob("*.py"):
        if any(part in {".venv", "__pycache__", "tests", "migrations"}
               for part in path.parts):
            continue
        if workspace in path.resolve().parents:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else None)
            if name != "emit":
                continue
            for kw in node.keywords:
                if kw.arg != "kind":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str
                ):
                    emitted.add(kw.value.value)

    assert emitted, "found no emitted notification kinds — the AST walk has gone stale"
    # Non-vacuity: the walk must see the kind this workstream added, or an empty-ish
    # result would make the assertion below pass for the wrong reason.
    assert "artifact_superseded" in emitted, (
        f"the AST walk did not find the supersession notice; found {sorted(emitted)}"
    )
    unknown = sorted(emitted - allowed)
    assert not unknown, (
        f"these notification kinds are emitted but refused by ck_notification_kind: "
        f"{unknown}. A refused INSERT aborts the caller's whole transaction, so this "
        "surfaces as an unrelated failure several frames away."
    )


async def test_the_parser_would_notice_a_missing_value():
    """Guards the two tests above from passing because the parse returned everything.
    A parser that silently produced a superset would make both vacuous."""
    allowed = await _allowed_values("ck_governance_request_type")
    assert "artifact_consumption" in allowed
    assert "definitely_not_a_request_type" not in allowed
