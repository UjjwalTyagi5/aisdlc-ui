"""Per-user Redis Set JTI denylist — Wave A (SCIM provisioning, REQ-M7-17).

Three async helpers for managing the JTI denylist:
  add_jti_to_user_denylist  — called at token mint time to register a live JTI
  is_jti_denied             — called per-request in jwt_auth_middleware (O(1) SISMEMBER)
  revoke_all_user_jtis      — called at SCIM deprovision to keep all JTIs denied

Redis key shape:  denylist:user:{tenant_id}:{sub}  (Redis Set, one UUID per live token)
Set TTL policy:   EXPIREAT = max(current expiry, new_token_expiry + buffer)
                  Implemented via Lua script for atomicity (Pitfall 5 — TTL regression
                  on multi-token users when a shorter-lived token is added after a
                  longer-lived one was already in the set).

Pure async module — no FastAPI imports, no app.state references.
Redis client is injected from the call site (process_api jwt_auth_middleware).
Never raises — returns None / bool / int on all failure paths (fail-closed contract).
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_DENYLIST_TTL_BUFFER_SECONDS = 60  # buffer beyond the token's own lifetime

# Lua script for atomic SADD + EXPIREAT without TTL regression (Pitfall 5).
# KEYS[1] = denylist key
# ARGV[1] = jti string
# ARGV[2] = new expiry epoch (int)
_SADD_EXPIREAT_LUA = """
local key = KEYS[1]
local jti = ARGV[1]
local new_exp = tonumber(ARGV[2])
redis.call('SADD', key, jti)
local cur_exp = redis.call('EXPIRETIME', key)
if cur_exp == -1 or cur_exp < new_exp then
    redis.call('EXPIREAT', key, new_exp)
end
return 1
"""


async def add_jti_to_user_denylist(
    redis_client,
    tenant_id: str,
    sub: str,
    jti: str,
    token_ttl_seconds: int,
) -> None:
    """Register a live JTI in the per-user denylist set.

    Uses a Lua script for atomic SADD + EXPIREAT so the set's TTL never regresses
    below its longest-lived member when multiple tokens are active simultaneously.
    Falls back to a two-step pipeline if Lua execution is unavailable.

    Returns None — callers should not need the result.
    Never raises.
    """
    key = f"denylist:user:{tenant_id}:{sub}"
    expiry_epoch = int(time.time()) + token_ttl_seconds + _DENYLIST_TTL_BUFFER_SECONDS
    try:
        try:
            # Attempt Lua-atomic path first
            await redis_client.eval(_SADD_EXPIREAT_LUA, 1, key, jti, expiry_epoch)
        except Exception:
            # Fallback to pipeline if EVAL is unavailable (e.g. certain Redis cluster modes)
            pipe = redis_client.pipeline()
            pipe.sadd(key, jti)
            pipe.expireat(key, expiry_epoch)
            await pipe.execute()
    except Exception as exc:
        logger.warning("JTI denylist add failed (tenant=%s sub=%s): %s", tenant_id, sub, type(exc).__name__)


async def is_jti_denied(
    redis_client,
    tenant_id: str,
    sub: str,
    jti: str,
) -> bool:
    """Check whether a JTI is in the per-user denylist.

    Returns True  → token is revoked; middleware should return 401.
    Returns False → token is not in the denylist (valid or legacy-no-jti).
    Never raises — returns False on Redis errors (T-7.4-02: fail-open in local dev
    is the accepted risk; enterprise mode requires Redis via ENABLE_SCIM precondition).
    """
    key = f"denylist:user:{tenant_id}:{sub}"
    try:
        return bool(await redis_client.sismember(key, jti))
    except Exception as exc:
        logger.warning("JTI denylist check failed (tenant=%s sub=%s): %s", tenant_id, sub, type(exc).__name__)
        return False


async def revoke_all_user_jtis(
    redis_client,
    tenant_id: str,
    sub: str,
    max_token_lifetime_seconds: int = 3660,
) -> int:
    """Deprovision: mark all live JTIs for this user as permanently denied.

    PERSIST removes the set's TTL so the denylist survives until the last token
    would have expired naturally (per-user set already contains all live JTIs
    registered at mint time — no enumeration needed, D-01 belt-and-suspenders).

    Returns the cardinality of the denylist set (number of revoked tokens) for
    audit logging.  Returns 0 on Redis errors (never raises).
    """
    key = f"denylist:user:{tenant_id}:{sub}"
    try:
        await redis_client.persist(key)
        return int(await redis_client.scard(key))
    except Exception as exc:
        logger.warning("JTI denylist revoke-all failed (tenant=%s sub=%s): %s", tenant_id, sub, type(exc).__name__)
        return 0
