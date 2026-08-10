"""Pure capability resolution + de-duplication (decisions DP4, DP5, D9).

One active provider per capability per agent. Precedence native > curated > byo,
overridable by an admin-chosen primary on non-core overlap. Native-only capabilities
are never satisfied by a byo provider. No DB/IO here — callers gather providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.capabilities import taxonomy

_TIER_RANK = {"native": 0, "curated": 1, "byo": 2}


@dataclass
class ResolvedToolset:
    active: dict[str, "CapabilityProvider"] = field(default_factory=dict)
    shadowed: list[tuple["CapabilityProvider", "CapabilityProvider"]] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)


def resolve(providers: "list[CapabilityProvider]", primary_overrides: "dict[str, str] | None" = None) -> ResolvedToolset:
    primary_overrides = primary_overrides or {}
    # Drop byo providers claiming native-only capabilities (defense in depth; DP5).
    candidates = [
        p for p in providers
        if not (p.tier == "byo" and taxonomy.is_native_only(p.capability))
    ]

    by_cap: dict[str, list] = {}
    for p in candidates:
        by_cap.setdefault(p.capability, []).append(p)

    result = ResolvedToolset()
    for cap, plist in by_cap.items():
        override_ref = primary_overrides.get(cap)
        winner = None
        if override_ref:
            winner = next((p for p in plist if p.ref == override_ref), None)
        if winner is None:
            winner = min(plist, key=lambda p: (_TIER_RANK.get(p.tier, 99), p.ref))
        result.active[cap] = winner
        for p in plist:
            if p is not winner:
                result.shadowed.append((p, winner))
        if winner.tool is not None:
            result.tools.append(winner.tool)
    return result
