from shared.capabilities import preflight
from shared.capabilities.resolution import ResolvedToolset
from shared.capabilities.providers import CapabilityProvider


def test_no_gap_when_all_required_provided():
    assert preflight.capability_gap(["a", "b"], {"a", "b", "c"}) == []


def test_gap_lists_missing_sorted():
    assert preflight.capability_gap(["b", "a", "c"], {"a"}) == ["b", "c"]


def test_gap_message_is_precise():
    msg = preflight.gap_message("development", ["repo.write"])
    assert "Development" in msg or "development" in msg
    assert "repo.write" in msg


def test_preflight_agent_uses_active_capabilities():
    resolved = ResolvedToolset()
    resolved.active = {
        c: CapabilityProvider(tier="native", capability=c, ref=c)
        for c in ["req.ingest", "req.quality.analyze", "req.gap.detect",
                  "story.generate", "story.ac.normalize", "nfr.elicit",
                  "doc.generate.brd", "doc.generate.risk", "traceability.map",
                  "req.payload.build", "board.read", "artifact.write"]
    }
    assert preflight.preflight_agent("requirements", resolved) == []


def test_preflight_agent_reports_missing():
    resolved = ResolvedToolset()
    resolved.active = {"board.read": CapabilityProvider(tier="native", capability="board.read", ref="x")}
    missing = preflight.preflight_agent("requirements", resolved)
    assert "req.ingest" in missing
