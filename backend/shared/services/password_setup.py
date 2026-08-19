"""Issue and consume single-use links for setting a password.

One mechanism, two moments — an `invite` after an Org Admin onboards somebody, and a
`reset` after they ask for a link. The difference is the wording of the email and the
lifetime of the token; the machinery is identical, which is why there is one module and
one table rather than two of each that drift.

WHAT IS STORED IS A HASH. The token goes in the email and nowhere else; the row holds
SHA-256 of it. A database dump therefore yields no working links. A live reset token IS a
credential, and a table of live credentials is what this refuses to be.

CONSUMPTION IS ATOMIC. `consume` marks the row used in the same `UPDATE ... WHERE
used_at IS NULL` that selects it, so two simultaneous presentations of the same link
cannot both succeed. Doing it as SELECT-then-UPDATE would leave exactly that race on the
one operation where it matters.

THE TOKEN NEVER IDENTIFIES ITS HOLDER TO A CALLER. `consume` returns a user id to the
route, which then sets that user's password. Nothing here echoes an email address back,
so a stolen link discloses no account.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

Purpose = Literal["invite", "reset"]

# 32 bytes of entropy, urlsafe-encoded to ~43 characters. Long enough that guessing is
# not a consideration and short enough to survive an email client wrapping the line.
_TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(
    session: AsyncSession, *, user_id: str, purpose: Purpose, ttl_hours: int
) -> str:
    """Create a token for `user_id` and return the RAW value, once.

    Any outstanding token for this user is retired first. Requesting a second link must
    invalidate the first: otherwise a forwarded or intercepted earlier email stays usable,
    and "I clicked the newest link" would not mean the older one is dead.
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires = datetime.now(tz=timezone.utc) + timedelta(hours=ttl_hours)

    await session.execute(
        text(
            "UPDATE password_reset_tokens SET used_at = now() "
            "WHERE user_id = :u AND used_at IS NULL"
        ),
        {"u": user_id},
    )
    # Housekeeping on the write path rather than a sweep job: rows that are both consumed
    # and long past expiry can tell nobody anything, and a cron that has to exist for the
    # table not to grow is a cron that will one day not be running.
    await session.execute(
        text(
            "DELETE FROM password_reset_tokens "
            "WHERE expires_at < now() - interval '30 days'"
        )
    )
    await session.execute(
        text(
            "INSERT INTO password_reset_tokens (user_id, token_hash, purpose, expires_at) "
            "VALUES (:u, :h, :p, :e)"
        ),
        {"u": user_id, "h": _hash(token), "p": purpose, "e": expires},
    )
    logger.info(
        "password %s token issued user=%s ttl=%dh", purpose, user_id, ttl_hours
    )
    return token


class Status:
    """Why a token was refused, for a page that has to say something useful.

    Distinguishing "already used" from "never existed" is deliberate and safe: the holder
    of the link already had it, so telling them it is spent reveals nothing they did not
    know, and the alternative is a dead end that generates a support ticket.
    """

    OK = "ok"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    USED = "used"


async def inspect(session: AsyncSession, token: str) -> str:
    """Report whether a token would be accepted, WITHOUT consuming it.

    For the set-password page to render "this link has expired" before the user types a
    password twice. Read-only by design — a page load must not spend the token, or
    a mail client that pre-fetches links would break every invite.
    """
    if not token:
        return Status.UNKNOWN
    row = (
        await session.execute(
            text(
                "SELECT used_at, expires_at FROM password_reset_tokens "
                "WHERE token_hash = :h"
            ),
            {"h": _hash(token)},
        )
    ).first()
    if row is None:
        return Status.UNKNOWN
    if row.used_at is not None:
        return Status.USED
    if row.expires_at <= datetime.now(tz=timezone.utc):
        return Status.EXPIRED
    return Status.OK


async def consume(session: AsyncSession, token: str) -> Optional[str]:
    """Spend the token and return its user id, or None if it was not usable.

    The guard clauses live in the UPDATE, so selecting and spending are one statement and
    a token cannot be redeemed twice by two concurrent requests.
    """
    if not token:
        return None
    row = (
        await session.execute(
            text(
                "UPDATE password_reset_tokens SET used_at = now() "
                "WHERE token_hash = :h AND used_at IS NULL AND expires_at > now() "
                "RETURNING user_id"
            ),
            {"h": _hash(token)},
        )
    ).first()
    if row is None:
        logger.info("password token rejected (unknown, expired or already used)")
        return None
    return row.user_id
