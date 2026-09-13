"""The audit actor filter must match the actors the response names.

Its own file, deliberately: test_audit_api.py carries a module-level
`pytestmark = pytest.mark.xfail(strict=False)` left over from "Wave 3", which has
long since shipped -- three of its tests xpass today. Anything added there is
reported as xpassed and gates nothing, which is the opposite of what a regression
test is for.
"""
from __future__ import annotations

import pytest

# ── The actor filter must match the actors the response NAMES ────────────────

def test_system_actor_filter_matches_null_actor_ids():
    """Choosing "system" in the Any-actor dropdown must not empty the table.

    `AuditEventOut` renders a null `actor_id` as the literal "system", and the UI
    builds its actor dropdown from the rows it was shown. The filter compared
    `actor_id = 'system'`, which no row can satisfy — system events store NULL. On
    the dev database that was 27 of 28 rows, so picking the only actor most events
    have returned nothing at all.
    """
    from shared.models.orm import AuditEvent
    from shared.routers.audit import _actor_matches

    clause = _actor_matches("system")
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "IS NULL" in rendered.upper(), rendered
    # And it must be the actor column it checks, not something else.
    assert "actor_id" in rendered, rendered
    assert AuditEvent.__tablename__ in rendered or "audit_events" in rendered


def test_a_named_actor_still_filters_by_equality():
    """The null-substitution must not swallow ordinary actor ids."""
    from shared.routers.audit import _actor_matches

    uid = "832bc0a6-f6a6-4879-a52c-24440e2ed817"
    rendered = str(_actor_matches(uid).compile(compile_kwargs={"literal_binds": True}))
    assert "IS NULL" not in rendered.upper(), rendered
    assert uid in rendered, rendered
