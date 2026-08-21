"""Shared async HTTP clients for connector calls.

Every connector used to open its own `httpx.AsyncClient(timeout=N)` per request. That
is expensive twice over:

  1. Constructing a client calls `ssl.create_default_context()`, which re-reads the
     entire system CA bundle off disk. Profiling the *idle* backend put ~14% of a
     saturated core in that single call — the connector health probes re-probe five
     SaaS connectors every 30s, and each probe rebuilt its trust store from scratch.
  2. The client is discarded after one request, so the TCP connection and TLS session
     go with it and the next call pays a full handshake.

Clients are cached PER EVENT LOOP, never globally. `httpx.AsyncClient` binds its
connection pool to the loop that created it, and this codebase runs agent tools inside
a transient `asyncio.run()` sub-loop — the same hazard that forces `NullPool` in
`shared/db.py`. A single module-level client would eventually be awaited from a loop
that did not create it and fail with "Event loop is closed" / "attached to a different
loop". Keying on the running loop gives each loop its own pool, and the
`WeakKeyDictionary` drops the entry once a sub-loop is garbage-collected.

The `SSLContext` is the one piece that IS safe to share outright: it carries no
event-loop affinity and is read-only once built, so it is created exactly once per
process and handed to every client. That is where most of the saving comes from.

Callers must NOT use `async with` on the returned client — it is shared, and closing it
would break every other caller on the same loop. Just call it:

    client = get_async_client(timeout=30)
    resp = await client.request(method, url, ...)
"""

from __future__ import annotations

import asyncio
import ssl
import weakref

import httpx

DEFAULT_TIMEOUT = 30.0

# Built once per process. An SSLContext is not loop-bound and is safe for concurrent
# reads, so every client on every loop can share this one instead of re-reading the
# CA bundle. This is the fix for the profiled `ssl.create_default_context` hot spot.
_SSL_CONTEXT: ssl.SSLContext = httpx.create_ssl_context()

# loop -> {(timeout, follow_redirects): AsyncClient}
_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def get_async_client(
    timeout: float = DEFAULT_TIMEOUT,
    *,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    """Return a shared `httpx.AsyncClient` for the running event loop.

    Do not close it and do not wrap it in `async with` — it outlives any single request
    and is reused by every caller on this loop. Clients are keyed by the settings that
    change connection behaviour, so a 10s-timeout caller never inherits a 60s client.
    """
    loop = asyncio.get_running_loop()
    per_loop = _clients.get(loop)
    if per_loop is None:
        per_loop = {}
        _clients[loop] = per_loop

    key = (timeout, follow_redirects)
    client = per_loop.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=_SSL_CONTEXT,
        )
        per_loop[key] = client
    return client


async def aclose_all() -> None:
    """Close every client belonging to the running loop.

    Called from the app's shutdown path. Only touches the current loop's clients —
    another loop's pool is not ours to close, and sub-loop clients are released when
    the loop itself is collected.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    per_loop = _clients.pop(loop, None) or {}
    for client in per_loop.values():
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — shutdown must not raise
            pass
