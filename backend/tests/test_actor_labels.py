"""The "who approved this" line must name a person, not a JWT subject.

Every actor column stores `request.state.user_id` — a UUID. The Documents list rendered
it raw, so an approved document read `09e55932-a6c1-4d8c-b6ed-7df2d013b879 · 9/7/2026`.

WHAT IS ACTUALLY WORTH ASSERTING HERE is not "a uuid becomes an email" — that is the easy
half and it would pass with a lookup that ignored the tenant entirely. `users` has NO
row-level security (checked against the live database: `relrowsecurity` false, zero
policies), so the tenant filter in the query is the only thing standing between an id and
another organisation's email address. That is the test below that would catch a real
regression.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.actor_labels import actor_labels, relabel  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _org_with_user(email: str):
    """An organisation and one user in it. Returns (org_id, user_id)."""
    org, user = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Actor Label Test')"
        ), {"i": org, "s": f"actor-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email, active) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :e, true)"
        ), {"i": user, "t": org, "e": email})
        await s.commit()
    return org, user


async def _labels(tenant_id, ids):
    async with get_db_session_superuser() as s:
        return await actor_labels(s, tenant_id, ids)


async def test_a_user_id_resolves_to_the_email():
    org, user = await _org_with_user("bruno@abcbank.com")
    assert (await _labels(org, [user])) == {user: "bruno@abcbank.com"}


async def test_an_id_from_another_tenant_does_not_resolve():
    """THE ONE THAT MATTERS. `users` is not RLS-protected, so an unscoped lookup would
    hand back another organisation's email. Asking for a real user id under the WRONG
    tenant must return nothing at all."""
    _, user = await _org_with_user("someone@othercorp.com")
    other_org, _ = await _org_with_user("unrelated@abcbank.com")

    assert (await _labels(other_org, [user])) == {}


async def test_an_unknown_id_is_left_alone_rather_than_blanked():
    """A departed approver must still render as SOMETHING. Dropping the value would make
    an approved document look unapproved, which is a worse lie than a raw id."""
    org, _ = await _org_with_user("bruno@abcbank.com")
    ghost = str(_uuid.uuid4())

    labels = await _labels(org, [ghost])
    assert labels == {}
    assert relabel(ghost, labels) == ghost


async def test_a_value_that_is_already_an_email_is_passed_through():
    """Older rows and the fixtures write an email into these columns directly. Those are
    already readable and must survive untouched."""
    org, _ = await _org_with_user("bruno@abcbank.com")

    labels = await _labels(org, ["legacy@abcbank.com"])
    assert labels == {}
    assert relabel("legacy@abcbank.com", labels) == "legacy@abcbank.com"


async def test_no_tenant_resolves_nothing():
    """Fail closed: without a tenant there is no safe scope to look in."""
    _, user = await _org_with_user("bruno@abcbank.com")
    assert (await _labels(None, [user])) == {}


async def test_the_whole_list_costs_one_query():
    """A Documents list calls this once, not once per row. Asserted by resolving several
    ids in a single call and getting all of them."""
    org, a = await _org_with_user("a@abcbank.com")
    b, c = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        for uid, mail in ((b, "b@abcbank.com"), (c, "c@abcbank.com")):
            await s.execute(text(
                "INSERT INTO users (id, tenant_id, email, active) "
                "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :e, true)"
            ), {"i": uid, "t": org, "e": mail})
        await s.commit()

    labels = await _labels(org, [a, b, c, None, "", a])
    assert labels == {a: "a@abcbank.com", b: "b@abcbank.com", c: "c@abcbank.com"}
