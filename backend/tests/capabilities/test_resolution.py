import pytest
from shared.capabilities.providers import CapabilityProvider
from shared.capabilities.resolution import resolve


def P(tier, cap, ref, tool=None):
    return CapabilityProvider(tier=tier, capability=cap, ref=ref, tool=tool)


def test_single_provider_wins():
    r = resolve([P("native", "board.read", "list_board_items", tool=object())])
    assert r.active["board.read"].ref == "list_board_items"
    assert len(r.tools) == 1


def test_precedence_native_over_curated_over_byo():
    r = resolve([
        P("byo", "quality.sast.scan", "srv-1", tool=object()),
        P("curated", "quality.sast.scan", "semgrep", tool=object()),
    ])
    assert r.active["quality.sast.scan"].tier == "curated"
    # loser recorded as shadowed, not dropped silently
    assert any(loser.tier == "byo" for loser, winner in r.shadowed)


def test_native_beats_everything():
    r = resolve([
        P("byo", "board.read", "srv-1", tool=object()),
        P("native", "board.read", "list_board_items", tool=object()),
    ])
    assert r.active["board.read"].tier == "native"


def test_admin_primary_override_wins_on_non_core_overlap():
    r = resolve(
        [
            P("curated", "quality.sast.scan", "semgrep", tool=object()),
            P("byo", "quality.sast.scan", "srv-9", tool=object()),
        ],
        primary_overrides={"quality.sast.scan": "srv-9"},
    )
    assert r.active["quality.sast.scan"].ref == "srv-9"


def test_native_only_capability_ignores_byo_provider():
    # A BYO provider claiming a native-only cap is never bound (defense in depth; DP5).
    r = resolve([P("byo", "repo.write", "srv-evil", tool=object())])
    assert "repo.write" not in r.active
    assert r.tools == []


def test_provider_without_tool_is_active_but_not_bound():
    # e.g. a curated tool whose managed server URL is unset in dev — counts for the
    # capability-gap check but contributes no bindable tool.
    r = resolve([P("curated", "req.quality.analyze", "req-quality-linter", tool=None)])
    assert "req.quality.analyze" in r.active
    assert r.tools == []


def test_deterministic_tie_break_on_same_tier_wins_alphabetically():
    # When two providers share the same tier (e.g. two BYO providers, no native/curated),
    # the winner is deterministic: alphabetically first by ref, NOT input order.
    # Test both orderings to confirm stability.
    providers_order_1 = [
        P("byo", "quality.sast.scan", "srv-z", tool=object()),
        P("byo", "quality.sast.scan", "srv-a", tool=object()),
    ]
    providers_order_2 = [
        P("byo", "quality.sast.scan", "srv-a", tool=object()),
        P("byo", "quality.sast.scan", "srv-z", tool=object()),
    ]
    r1 = resolve(providers_order_1)
    r2 = resolve(providers_order_2)
    # Both orderings should pick "srv-a" (alphabetically first).
    assert r1.active["quality.sast.scan"].ref == "srv-a"
    assert r2.active["quality.sast.scan"].ref == "srv-a"
