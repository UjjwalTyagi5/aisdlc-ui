r"""Read, write, or both — the access level attached to a connector grant.

WHY A LEVEL AND NOT A BOOLEAN. `integration_grants` recorded THAT a Business Unit
may use Jira and never HOW. Every grant was therefore implicitly read+write, which
is the widest thing it could have meant: an agent granted Jira to read a backlog
could also transition items and post comments, and nothing anywhere said it should
not. Connectors already separate `read_adapter` from `write_adapter` — the split
existed at the point of use and had no counterpart at the point of permission.

THE LATTICE IS TINY AND THAT IS DELIBERATE. Three levels, ordered by the set of
operations each admits:

        read_write
        /        \
     read        write
        \        /
         (nothing)

`read` and `write` are INCOMPARABLE — neither contains the other. That is what
makes this a lattice rather than a ladder, and it is why `permits()` is a subset
test rather than a `>=` on an integer. Ranking them 1/2/3 would quietly make
`write` imply `read`, which is exactly the escalation the level exists to prevent.

INHERITANCE IS INTERSECTION. A project's access is the intersection of what it
asked for with what its unit was granted, and a unit's with what the organisation
onboarded. Intersection rather than "the lower one" because of the incomparable
pair: read ∩ write is EMPTY, not "one of them". A unit granted read-only that
tries to give a project write access yields no access at all, which is the correct
and safe answer — and `narrow()` returns None to say so rather than picking a side.

NOTHING HERE TOUCHES THE DATABASE. It is pure set arithmetic so it can be unit
tested exhaustively and called from the request path, the runtime path, and the
migration alike.
"""
from __future__ import annotations

from typing import Iterable, Literal, Optional

AccessLevel = Literal["read", "write", "read_write"]

READ: AccessLevel = "read"
WRITE: AccessLevel = "write"
READ_WRITE: AccessLevel = "read_write"

#: Every valid level, for CHECK constraints and request validation.
ACCESS_LEVELS: tuple[AccessLevel, ...] = (READ, WRITE, READ_WRITE)

#: The default for a NEW grant. Least privilege: an Org Admin who wants a unit to
#: write must say so. Existing rows were backfilled to read_write by migration 0023
#: instead, because tightening them silently would have stopped agents mid-flight.
DEFAULT_ACCESS: AccessLevel = READ

#: A level expanded into the operation modes it admits. This mapping IS the lattice.
_MODES: dict[str, frozenset[str]] = {
    READ: frozenset({"read"}),
    WRITE: frozenset({"write"}),
    READ_WRITE: frozenset({"read", "write"}),
}


def is_access_level(value: object) -> bool:
    """True for one of the three catalogued levels. Anything else is not a level."""
    return isinstance(value, str) and value in _MODES


def modes(level: str) -> frozenset[str]:
    """The operation modes a level admits. Unknown levels admit NOTHING.

    Fail-closed on the unknown value rather than raising: this is called from the
    runtime path with a level read out of the database, and a row that somehow holds
    a bad level should deny rather than crash a run mid-flight. The API layer
    validates on the way in, which is where a bad level should be rejected loudly.
    """
    return _MODES.get(level, frozenset())


def permits(level: Optional[str], mode: str) -> bool:
    """May a holder of `level` perform a `mode` ('read' | 'write') operation?

    `None` is "no grant at all" and permits nothing — the same answer as a level
    nobody recognises. Callers must not distinguish them; both mean no.
    """
    if level is None:
        return False
    return mode in modes(level)


def from_modes(allowed: Iterable[str]) -> Optional[AccessLevel]:
    """The level admitting exactly this mode set, or None for the empty set.

    The inverse of `modes()`, and the only place a level is constructed from parts.
    """
    wanted = frozenset(m for m in allowed if m in ("read", "write"))
    for level, level_modes in _MODES.items():
        if level_modes == wanted:
            return level  # type: ignore[return-value]
    return None


def narrow(parent: Optional[str], child: Optional[str]) -> Optional[AccessLevel]:
    """The access a child level actually yields under a parent — their intersection.

    THE ONE RULE THE HIERARCHY RESTS ON: Organization → BU → project → agent, each
    level able to narrow and never to widen. A project asking for read_write under a
    unit granted read gets read; a project asking for write under a unit granted read
    gets None, because those two share no operation at all.

    Returns None when the intersection is empty, which callers must treat as "no
    access" rather than as an error — an empty intersection is a legitimate outcome
    of two honest decisions, not a fault.
    """
    if parent is None or child is None:
        return None
    return from_modes(modes(parent) & modes(child))


def contains(parent: Optional[str], child: Optional[str]) -> bool:
    """Does `parent` admit everything `child` does? The escalation test.

    Used at WRITE time by the API: a BU Admin selecting a project's access, or a
    Project Admin choosing their own, must pass this against the level above them.
    `narrow()` answers what they would get; this answers whether to refuse them.
    Refusing is better than silently narrowing when somebody has explicitly asked
    for more than they may have — they should be told, not quietly given less.
    """
    if child is None:
        return True  # asking for nothing is always within any grant
    if parent is None:
        return False
    return modes(child) <= modes(parent)


def label(level: Optional[str]) -> str:
    """Human phrasing, for refusal messages and audit rows."""
    return {
        READ: "read-only",
        WRITE: "write-only",
        READ_WRITE: "read and write",
    }.get(level or "", "no access")
