"""One place to build a Redis client. Everything it needs comes from REDIS_URL.

TWO PROBLEMS THIS SOLVES.

**Bounded failure.** Every Redis user on this platform is deliberately fail-open — the
JTI denylist "never raises, returns False on Redis errors"; the token-epoch staleness
check lets the request through; the audit dead-letter write is swallowed; the usage meter
drops a sample. That is the right posture: Redis being down should degrade the platform,
not stop it.

But `aioredis.from_url(REDIS_URL)` with no timeout does not fail open. It fails open
EVENTUALLY — after the OS-level connect timeout, roughly 30 seconds against an
unreachable host. The auth middleware makes two such calls per request, so an unreachable
Redis turned every authenticated request into a ~60-second hang that still answered
correctly at the end. That is why it read as a hang rather than an error, and why nothing
in the logs looked fatal: two WARNINGs, a minute apart, saying a check "failed".
`_probe_redis` in process_api.py already passed `socket_connect_timeout=2` for exactly
this reason — it was right, it was just one call site out of nine.

**One knob.** Moving to a managed Redis should be a change to REDIS_URL and nothing else:
no code edit, no second environment variable to remember, no per-call-site flag. So every
connection detail is carried in the URL:

    redis://localhost:6379/0                        plain, local
    rediss://:<access-key>@host.redis.azure.net:10000/0
                                                    TLS + password, single node
    rediss://:<access-key>@host.redis.azure.net:10000/0?cluster=true
                                                    TLS + password, OSS cluster mode

`rediss://` selects TLS, `:<password>@` supplies the access key, the port is the port —
redis-py derives all of that from the URL already. The only thing it cannot infer is
whether the endpoint speaks OSS cluster protocol, because that is a property of the
server rather than the address; `?cluster=true` is that one bit, and it travels IN the
URL so the URL stays the only thing to change.

CLUSTER CAVEATS, since the flag makes them reachable. Every operation this codebase
performs is single-key (`get`/`set`/`setex`/`getdel`/`incr`/`incrby`/`incrbyfloat`/
`expire`/`sismember`/`scard`/`persist`/`xadd`), plus `publish` and pipelines that only
ever touch one key — all of which route cleanly by slot. A future multi-key operation
across different slots would need a hash tag. Cluster mode also has no numbered
databases, so the path must be `/0`.

NOT EVERY CALLER IS FAIL-OPEN. The denylist, token-epoch check, audit dead-letter write
and usage meter all swallow failures. `config/auth/ws_ticket.py` does not — mint and
redeem raise, so with no reachable Redis every agent WebSocket fails to authenticate
while the REST API carries on. Redis is optional for the API and required for the agents,
and the bounded timeouts below change how fast that is discovered, not whether it is true.

A SHARED REDIS NEEDS A KEY NAMESPACE. This module does not add one. Pointing REDIS_URL at
an instance another product also uses puts `denylist:user:*`, token epochs, TPM and cost
counters and the `audit:dead_letter` stream in a keyspace someone else is writing to,
under names generic enough to collide. Give this platform its own instance, its own
logical database, or add a key prefix before sharing one.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import redis.asyncio as aioredis

from config.env import REDIS_URL

# Deliberately short. Every caller is on the request path and every caller has a working
# answer for "Redis did not respond", so waiting is worth less than proceeding. A healthy
# Redis answers in single-digit milliseconds; approaching two seconds is already an
# outage rather than slowness.
CONNECT_TIMEOUT_S = 2.0
OPERATION_TIMEOUT_S = 2.0

# Query parameter that selects the cluster client. Consumed here and stripped before the
# URL reaches redis-py, which would reject it as an unknown connection argument.
_CLUSTER_PARAM = "cluster"
_TRUTHY = {"1", "true", "yes", "on"}


def _split_cluster_flag(url: str) -> tuple[str, bool]:
    """Return the URL with `cluster=` removed, and whether it asked for cluster mode."""
    if not url:
        return url, False
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    cluster = any(k.lower() == _CLUSTER_PARAM and v.lower() in _TRUTHY for k, v in query)
    remaining = [(k, v) for k, v in query if k.lower() != _CLUSTER_PARAM]
    return urlunsplit(parts._replace(query=urlencode(remaining))), cluster


def is_cluster_url(url: str | None = None) -> bool:
    """Whether this URL asks for cluster mode. Exposed for diagnostics and tests."""
    return _split_cluster_flag(url if url is not None else REDIS_URL)[1]


def redis_from_url(url: str | None = None, **overrides: Any):
    """Build an async Redis client with bounded timeouts, single-node or cluster.

    Prefer this over calling `aioredis.from_url` directly: that default waits as long as
    the operating system will, which is the hang described above, and it cannot reach the
    cluster client at all.
    """
    target, cluster = _split_cluster_flag(url if url is not None else REDIS_URL)

    settings: dict[str, Any] = {
        "socket_connect_timeout": CONNECT_TIMEOUT_S,
        "socket_timeout": OPERATION_TIMEOUT_S,
    }
    settings.update(overrides)

    if cluster:
        # Imported lazily: the cluster client pulls in extra machinery that a
        # single-node deployment never needs to load.
        from redis.asyncio.cluster import RedisCluster  # noqa: PLC0415

        return RedisCluster.from_url(target, **settings)

    return aioredis.from_url(target, **settings)
