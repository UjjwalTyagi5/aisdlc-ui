"""Turn the stored actor id into something a person can read.

Every "who did this" column on this product — `artifacts.approved_by`,
`artifacts.uploaded_by`, `artifact_versions.produced_by`, `.published_by` — is written
from `request.state.user_id`, which is the JWT `sub`: a UUID. Shown raw, the Documents
list reads

    09e55932-a6c1-4d8c-b6ed-7df2d013b879 · 9/7/2026

which answers "who approved this" with a string nobody can match to a colleague.

WHY THE COLUMN KEEPS THE ID AND ONLY THE RESPONSE CARRIES THE EMAIL. An email is a
mutable handle: people get renamed, addresses get reassigned. Storing the email at
approval time would freeze whatever it was that day, and an audit trail that says
`j.smith@` after the address has moved to a different person is worse than a UUID,
because it looks authoritative. The id is the stable anchor; the email is a rendering of
it, resolved fresh on every read.

THE TENANT FILTER IS THE POINT, NOT A FORMALITY. `users` has no row-level security —
verified against the live database, `relrowsecurity` is false and there are no policies —
so an unfiltered `WHERE id = ...` here would happily return another tenant's email. The
ids fed in come from our own tenant-scoped rows today, so this is defence in depth rather
than a live hole, but it is exactly the kind of lookup that gets reused somewhere less
careful. Scoping costs one AND.

ALREADY AN EMAIL? PASS IT THROUGH. Older rows and the test fixtures write an email into
these columns directly. Those are already readable and must survive untouched.

THE DISCRIMINATOR IS `@`, NOT "does this parse as a UUID". `users.id` is a VARCHAR, not a
uuid column — so a perfectly valid user id need not be UUID-shaped, and a UUID test would
silently refuse to resolve it while looking like it worked. Asking the narrower question
("is this already human-readable?") is the one that survives the column's actual type.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _needs_lookup(value: str) -> bool:
    """False for anything already readable — an email is its own label."""
    return "@" not in value


async def actor_labels(
    db: AsyncSession, tenant_id: str | None, ids: Iterable[str | None],
) -> Mapping[str, str]:
    """Map each actor id to the email behind it.

    Returns a mapping that only contains ids it RESOLVED. A caller is expected to fall
    back to the id it already has — a deleted user must still render as something, and
    silently blanking the approver would make an approved document look unapproved.

    One statement for the whole list: this is called once per response, not once per row.
    """
    wanted = {i for i in ids if i and _needs_lookup(i)}
    if not wanted or not tenant_id:
        return {}

    rows = (await db.execute(
        text(
            # `id` compared AS TEXT: the column is varchar, and casting the array to
            # uuid[] fails outright with "operator does not exist: character varying =
            # uuid". `tenant_id` really is a uuid, hence the asymmetry.
            "SELECT id, email FROM users "
            "WHERE id = ANY(CAST(:ids AS text[])) "
            "  AND tenant_id = CAST(:t AS uuid)"
        ),
        {"ids": list(wanted), "t": tenant_id},
    )).mappings().all()

    return {r["id"]: r["email"] for r in rows if r["email"]}


def relabel(value: str | None, labels: Mapping[str, str]) -> str | None:
    """`value` as an email when we know one, otherwise `value` untouched."""
    if not value:
        return value
    return labels.get(value, value)
