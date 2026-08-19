"""Which access levels can a connector actually honour?

A grant records an intention; the connector decides what is possible. Those two
disagreed silently and in both directions:

  Slack has NO read capabilities, so `read` — our least-privilege default — grants
  a connector that can do nothing at all, and the Integrations page shows a healthy
  grant next to it.

  Figma has NO write capabilities and says so in its own docstring, so a `write` or
  `read_write` grant on it is a level that can never be exercised.

REFUSE ONLY ON POSITIVE KNOWLEDGE. `supported_modes` returns None when a connector
cannot be introspected, and None must never refuse anything. Blocking a grant because
we could not read a manifest would make an unconstructable connector ungrantable —
and there is one on this branch right now (the factory passes `org_url=` to
`SlackConnector.__init__`, which does not take it). A validation that breaks admin
work when it malfunctions is worse than the mismatch it was added to catch.

INTROSPECTION USES THE CLASS, NOT THE FACTORY, for that same reason. A manifest is
pure metadata — `BaseConnector.capability_manifest` is documented as "which
read/write/listen capabilities are implemented" — so it needs no credentials, no
tenant and no org URL. Going through `get_connector_for_session` would drag all three
in and fail for reasons that have nothing to do with capabilities.

Results are cached: a manifest is a property of the code, not of a tenant or a
moment, so the first call per kind is the only one that costs anything.
"""
from __future__ import annotations

import logging
from typing import Optional

from shared.authz.connector_access import AccessLevel, from_modes, modes

logger = logging.getLogger(__name__)

#: kind -> the modes its manifest declares, or None for "could not tell".
_CACHE: dict[str, Optional[frozenset[str]]] = {}


def _introspect(kind: str) -> Optional[frozenset[str]]:
    """The modes `kind` implements, or None when it cannot be determined."""
    try:
        from config.connector_factory import _CONNECTOR_REGISTRY, _load_connector_class
    except Exception:  # noqa: BLE001
        return None

    dotted = _CONNECTOR_REGISTRY.get(kind)
    if not dotted:
        # Not a connector we ship. MCP servers come through the same grant table and
        # have no manifest at all, so "unknown" is the honest answer for them too.
        return None

    try:
        cls = _load_connector_class(dotted)
    except Exception:  # noqa: BLE001
        logger.warning("connector %s could not be imported for introspection", kind)
        return None

    # Constructors differ — some take org_url positionally, some by keyword, some
    # take nothing. None of them need it to answer what they implement.
    instance = None
    for build in (lambda: cls(), lambda: cls(org_url=""), lambda: cls("")):
        try:
            instance = build()
            break
        except Exception:  # noqa: BLE001
            continue
    if instance is None:
        logger.warning("connector %s could not be instantiated for introspection", kind)
        return None

    try:
        manifest = instance.capability_manifest()
    except Exception:  # noqa: BLE001
        logger.warning("connector %s has no readable capability manifest", kind)
        return None

    found = set()
    if manifest.read_capabilities:
        found.add("read")
    if manifest.write_capabilities:
        found.add("write")
    return frozenset(found)


def supported_modes(kind: str) -> Optional[frozenset[str]]:
    """Cached `_introspect`. None means "could not tell" and must not refuse."""
    if kind not in _CACHE:
        _CACHE[kind] = _introspect(kind)
    return _CACHE[kind]


def supported_level(kind: str) -> Optional[AccessLevel]:
    """The widest level `kind` can honour, or None when unknown or capability-less."""
    found = supported_modes(kind)
    if found is None:
        return None
    return from_modes(found)


def unsupported_reason(kind: str, access: str) -> Optional[str]:
    """Why `kind` cannot honour `access`, or None when it can (or when unknown).

    Returns a sentence for a human, because this refusal is shown to an admin who is
    trying to give somebody access and needs to know what to do instead.
    """
    found = supported_modes(kind)
    if found is None:
        return None  # unknown — never refuse

    wanted = modes(access)
    missing = wanted - found
    if not missing:
        return None

    if not found:
        return (
            f"{kind} declares no read or write capabilities, so no access level "
            "can be exercised against it."
        )

    have = " and ".join(sorted(found))
    lacks = " and ".join(sorted(missing))
    return (
        f"{kind} has no {lacks} capabilities — it supports {have} only. "
        f"Grant {have} access instead."
    )


def warnings_for(kind: str, access: str) -> list[str]:
    """Non-blocking notes about a level that is permitted but partly hollow.

    The board connectors are the case: `create_item` needs no identifier, but
    `add_comment`, `move_item_state` and `update_item_fields` all act on an item
    somebody had to find first — and the only way to find one is a read through this
    same connector. A write-only board grant is therefore real but narrow, and an
    admin choosing it should be told which half they are getting rather than
    discovering it when an agent fails mid-run.
    """
    if access != "write":
        return []
    found = supported_modes(kind)
    if found is None or found != {"read", "write"}:
        return []
    return [
        f"{kind} can create new items with write-only access, but updating or "
        "commenting on an existing one needs its id, which only a read can supply."
    ]
