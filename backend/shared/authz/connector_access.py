r"""Read, write, or both — the access level attached to a connector grant.

WHERE THE LEVEL LIVES. On the project's STAGE, in `projects.tool_access_modes` —
not on the unit's grant, which since migration 0024 records only that a Business
Unit may reach an integration at all. Connectors separate `read_adapter` from
`write_adapter`, and this is the vocabulary that decides which of the two an agent
gets. `config/connectors/scoped.ScopedConnector` is what enforces it.

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

THERE IS NO INTERSECTION STEP ANY MORE. This module used to carry `narrow()` and
`contains()` — set intersection, and the "does the parent admit everything the
child does" test — because a level was resolved by intersecting a project's ask
with its unit's grant. Migration 0024 removed that ceiling: the stage's level is
the answer, with nothing above it to intersect against. Both functions were
deleted rather than left unused, because a lattice operation sitting in an authz
module reads as load-bearing whether or not anything calls it.

If a ceiling is ever wanted back, the operation to restore is intersection and NOT
a `>=` on a rank — see the incomparability note above, which is the whole reason
this was ever a lattice.

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


def label(level: Optional[str]) -> str:
    """Human phrasing, for refusal messages and audit rows."""
    return {
        READ: "read-only",
        WRITE: "write-only",
        READ_WRITE: "read and write",
    }.get(level or "", "no access")


# ── the per-stage tool mode ───────────────────────────────────────────────────
#
# The UI calls the widest level "both"; this module calls it "read_write". They are
# the same lattice point and the difference is only vocabulary, so the translation
# lives here rather than being re-derived at each boundary that meets it.
#
# The stage mode is stored in `projects.tool_access_modes` (migration 0024), keyed
# "{agent_id}::{connector|mcp}::{target_ref}" — see accessModeKey() in
# frontend/components/app/tools-stage-picker.tsx, which is the only writer of it.

#: The vocabulary the stage picker sends. Kept separate from ACCESS_LEVELS so a
#: payload is validated against what the client actually says, not what we store.
TOOL_ACCESS_MODES: tuple[str, ...] = ("read", "write", "both")

#: What a stage's mode means when the picker never set one. The picker documents an
#: unset chip as "both", and with the unit ceiling gone that default IS the answer —
#: so it is written down here rather than left implicit at the resolver.
DEFAULT_TOOL_MODE: str = "both"


def stage_mode_key(agent_id: str, kind: str, target_ref: str) -> str:
    """The composite key a stage's mode is stored under. Mirrors accessModeKey()."""
    return f"{agent_id}::{kind}::{target_ref}"


def level_from_mode(mode: Optional[str]) -> Optional[AccessLevel]:
    """Translate a picker mode into a stored level. Unknown modes yield None.

    Fail-closed on the unrecognised value for the same reason `modes()` does: this is
    called from the runtime path with a value read out of JSONB, which has no CHECK
    constraint behind it to guarantee the vocabulary.
    """
    if mode == "both":
        return READ_WRITE
    if mode in (READ, WRITE):
        return mode  # type: ignore[return-value]
    return None


def mode_from_level(level: Optional[str]) -> Optional[str]:
    """The inverse, for handing a stored level back to the picker."""
    if level == READ_WRITE:
        return "both"
    if level in (READ, WRITE):
        return level
    return None
