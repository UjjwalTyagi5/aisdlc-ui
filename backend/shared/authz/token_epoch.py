"""Make a revocation bite now, instead of when the token happens to lapse.

THE PROBLEM. Permissions are resolved once at login and baked into the JWT; every
`require_permission` reads that claim. Nothing re-checked it, so removing a role, or
retuning what a role grants, had no effect until the holder's token expired. Deleting an
`org_admin` binding left the badge and the access intact — observed live, and logged as
P1 in `docs/rbac-auth-plan.md` long before this module existed.

WHY NOT THE OBVIOUS FIXES.

  · *Re-resolve permissions per request.* Correct, and what the enterprise OIDC path
    already does — but it puts a database round trip on the hot path of every request,
    which is the cost the baked claim exists to avoid.
  · *Denylist the user's live tokens.* The JTI denylist cannot enumerate them: it records
    a jti when one is revoked, never when one is minted, so there is no set of "this
    user's live tokens" to walk.

WHAT THIS DOES INSTEAD. One integer per user: the moment their access last changed. A
token carries `iat`; if it was minted before that moment, the permissions in it are known
to be stale and the request is refused. One Redis GET, no database, and it invalidates
every token that user holds at once without needing to know what they are.

The refusal is a 401 with a distinct code, not a 403: the caller is not forbidden from
what they asked, their token is out of date and they need a new one. A 403 would tell an
admin who had just been granted a role that they still lacked it.

FAIL-OPEN ON REDIS ERRORS, deliberately and in line with the JTI denylist
(`shared/auth/denylist.py`): the alternative makes Redis a hard dependency for every
authenticated request, so a blip takes the whole product down rather than briefly
extending a revocation window that used to be an hour wide by default.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Long enough to outlive any token that could predate a bump. Tokens are minted at 60
# minutes (config/auth/jwt.py); the margin covers clock skew and a longer-lived token
# being introduced without anyone remembering this constant.
_EPOCH_TTL_SECONDS = 4 * 60 * 60

# Keyed by event loop, NOT a single module global. redis.asyncio binds its connection
# pool to the loop that created it, so a client memoised across loops raises RuntimeError
# on use — and because every failure here is swallowed, that would present as the
# staleness check silently never firing. Anything with more than one loop in a process
# (the test client, a worker that runs its own) would have lost revocation entirely
# without saying so.
_clients: dict[object, object] = {}


def _now_epoch() -> int:
    """The instant to record, rounded UP to the next whole second.

    `iat` is second-granular — a token minted at any point during second N carries
    `iat = N`. If a revocation during that same second recorded N, the comparison
    `iat < epoch` would be `N < N`, false, and the token would survive: grant, mint,
    revoke in quick succession is exactly the sequence a test or an admin correcting a
    mistake produces, and it is the one case that must not slip through.

    Rounding up makes any token minted in that second or earlier stale. The cost is that
    a token minted later in the same second but genuinely AFTER the change is also
    refused — one re-login, transient, and in the safe direction.
    """
    return int(time.time()) + 1


def _redis():
    """Lazily build a client from REDIS_URL, or None when unset.

    Module-level rather than taken from `app.state` because the writers are not all in
    request context: `grant.py` is importable from an operator CLI and from workers, and
    a bump that only happened over HTTP would leave exactly the paths that matter — a
    revocation run by hand — silently not invalidating anything.
    """
    import asyncio  # noqa: PLC0415

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop in _clients:
        return _clients[loop]

    client = None
    try:
        from config.env import REDIS_URL  # noqa: PLC0415 - keeps import cheap

        if not REDIS_URL:
            logger.info("token_epoch: REDIS_URL unset — stale-token checks disabled")
        else:
            import redis.asyncio as aioredis  # noqa: PLC0415

            client = aioredis.from_url(REDIS_URL)
    except Exception as exc:  # pragma: no cover - import/config failure
        logger.warning("token_epoch: no Redis client (%s)", type(exc).__name__)
    _clients[loop] = client
    return client


def _key(tenant_id: str, user_id: str) -> str:
    return f"authz:epoch:{tenant_id}:{user_id}"


async def bump_user_epoch(tenant_id: str, user_id: str, *, exact: bool = False) -> None:
    """Mark this user's access as having been REDUCED. Never raises.

    Call after any write that takes something away — a binding revoked, a role's
    permission set narrowed, a custom role edited or deleted.

    Deliberately NOT called on a plain grant. Granting only widens what this user may do,
    so a token minted beforehand is stale in the harmless direction — under-privileged,
    and corrected at their next sign-in. Bumping there would buy no security and would,
    because `_now_epoch` rounds up, refuse a token minted in the same second as the grant:
    exactly the grant-then-sign-in sequence.

    `exact=True` records the current second instead of the next one. Use it ONLY where the
    credential itself changed — a password set or reset — because there the rounding is
    both unnecessary and actively harmful:

      · unnecessary, because a token minted in that same second must have come from a
        login using the NEW password; the old one already stopped working.
      · harmful, because rounding up refuses the user's own fresh session. Somebody who
        sets a password and signs in immediately is the ordinary path, not an edge case,
        and it was 401ing them.

    Everywhere else the default rounding is correct: an ordinary revocation leaves the
    old credential working, so a token minted in the same second may well predate it.
    """
    if not tenant_id or not user_id:
        return
    client = _redis()
    if client is None:
        return
    try:
        stamp = int(time.time()) if exact else _now_epoch()
        await client.set(_key(tenant_id, user_id), stamp, ex=_EPOCH_TTL_SECONDS)
    except Exception as exc:
        logger.warning(
            "token_epoch: bump failed (tenant=%s user=%s): %s",
            tenant_id, user_id, type(exc).__name__,
        )


async def bump_many(tenant_id: str, user_ids: list[str]) -> int:
    """Bump a set of users in one round trip. Returns how many were marked.

    For a role-permission override, which changes what a role grants and therefore
    affects everyone holding it. Bumping only the editor would leave every other holder
    on their old permission set.
    """
    ids = [u for u in dict.fromkeys(user_ids) if u]
    if not tenant_id or not ids:
        return 0
    client = _redis()
    if client is None:
        return 0
    try:
        now = _now_epoch()
        pipe = client.pipeline()
        for user_id in ids:
            pipe.set(_key(tenant_id, user_id), now, ex=_EPOCH_TTL_SECONDS)
        await pipe.execute()
        return len(ids)
    except Exception as exc:
        logger.warning(
            "token_epoch: bulk bump failed (tenant=%s n=%d): %s",
            tenant_id, len(ids), type(exc).__name__,
        )
        return 0


async def is_token_stale(tenant_id: str, user_id: str, issued_at: Optional[int]) -> bool:
    """True when this token was minted before the caller's access last changed.

    `issued_at` is the token's `iat`. Tokens minted before that claim existed pass
    unchecked — the same accommodation the JTI check makes for tokens minted before
    `jti` — because refusing them would sign out everyone on deploy, and they expire
    within the hour anyway.

    Returns False on any failure: no Redis, no epoch recorded, unreadable value.
    """
    if not issued_at or not tenant_id or not user_id:
        return False
    client = _redis()
    if client is None:
        return False
    try:
        raw = await client.get(_key(tenant_id, user_id))
        if raw is None:
            return False
        return int(issued_at) < int(raw)
    except Exception as exc:
        logger.warning(
            "token_epoch: staleness check failed (tenant=%s user=%s): %s",
            tenant_id, user_id, type(exc).__name__,
        )
        return False


def _reset_client_for_tests() -> None:
    """Drop the memoised clients so a test can point the module at a fake."""
    _clients.clear()
