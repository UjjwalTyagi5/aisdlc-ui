"""One-off cleanup: remove exactly the accounts scripts/seed_dev_personas.py creates.

Not a general-purpose user-delete tool — the email list below is a literal copy of
PERSONAS from seed_dev_personas.py, kept in sync manually since this script is meant
to be run once and deleted, not maintained.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from shared.db import get_db_session_superuser

SEEDED_EMAILS = [
    "orgadmin@abcbank.com", "farah@abcbank.com", "marcus@abcbank.com",
    "ana@abcbank.com", "priya@abcbank.com", "iris@abcbank.com",
    "diego@abcbank.com", "ingrid@abcbank.com", "hana@abcbank.com",
    "lena@abcbank.com", "bruno@abcbank.com", "luca@abcbank.com",
    "sofia@abcbank.com", "amara@abcbank.com",
]


async def main() -> None:
    async with get_db_session_superuser() as s:
        rows = (await s.execute(
            text("SELECT id, email FROM users WHERE lower(email) = ANY(:emails)"),
            {"emails": SEEDED_EMAILS},
        )).fetchall()
        if not rows:
            print("Nothing to remove — none of the seeded persona emails exist.")
            return
        user_ids = [str(r.id) for r in rows]
        await s.execute(
            text("DELETE FROM role_bindings WHERE user_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        result = await s.execute(
            text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": user_ids},
        )
        for r in rows:
            print(f"  - removed {r.email}")
        print(f"\n{result.rowcount} account(s) removed (role_bindings cleaned first).")


if __name__ == "__main__":
    asyncio.run(main())
