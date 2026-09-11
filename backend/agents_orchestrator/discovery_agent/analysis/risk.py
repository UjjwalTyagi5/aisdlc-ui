"""Migration risk per module: a score from 0 to 100, a tier, and the reasons.

EVERY POINT IS ATTRIBUTED. A score the BA (Discovery's owner on Track 3) is asked to
accept as a planning baseline has to be arguable line by line, so each factor that
contributed is recorded with its points and a sentence saying why. Nothing here is
learned or model-generated.

Factors and weights (caps in brackets):

  size          lines of code: <500 → 2, <2k → 6, <10k → 12, <50k → 16, else 20   [20]
  runtime       eol → 15, approaching → 8, legacy → 5                              [15]
  platform      10 per platform-bound API (WebForms, WCF server, WinForms, WPF,
                .NET Remoting, System.Web)                                         [25]
  dependencies  one point per three external packages                             [10]
  deprecated_dependencies  5 per deprecated package                                [10]
  vulnerable_dependencies  10 per high/critical, 4 per other                       [10]
  coupling      2 per module in the repository that depends on this one            [10]
  tests         5 when nothing in the repository tests this module                  [5]

Tiers — which kind of work moves the module (help/track3-agent-build-plan.md):

  manual        a hard platform blocker (WebForms, WCF server, Remoting — a rewrite,
                not a port) or a score of 70 and above
  mechanical    no platform blocker, score under 35, and the target is the same
                language — codemod/upgrade tooling (upgrade-assistant, OpenRewrite)
                can do most of the work
  llm_assisted  everything else: a rewrite with the legacy source and the module's
                equivalence criteria in context
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agents_orchestrator.discovery_agent.analysis.eol import RuntimeStatus
from agents_orchestrator.discovery_agent.analysis.inventory import HARD_BLOCKERS, ModuleFacts

TIERS = ("mechanical", "llm_assisted", "manual")

_BLOCKER_LABEL = {
    "webforms": "ASP.NET WebForms pages (no equivalent on .NET 5+ — the UI must be rewritten)",
    "wcf_server": "WCF service endpoints (.svc) — .NET 5+ has no WCF server; CoreWCF or gRPC/REST",
    "remoting": ".NET Remoting — removed from modern .NET",
    "winforms": "Windows Forms — runs on .NET 6+ but stays Windows-only",
    "wpf": "WPF — runs on .NET 6+ but stays Windows-only",
    "system_web": "System.Web (HttpContext, modules, handlers) — replaced by ASP.NET Core middleware",
}


@dataclass
class ModuleRisk:
    score: int
    tier: str
    factors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": self.score, "tier": self.tier, "factors": list(self.factors)}


def _size_points(loc: int) -> int:
    if loc < 500:
        return 2
    if loc < 2_000:
        return 6
    if loc < 10_000:
        return 12
    if loc < 50_000:
        return 16
    return 20


def score_module(
    facts: ModuleFacts,
    *,
    runtime_status: RuntimeStatus,
    dependency_count: int,
    deprecated: int,
    vulnerable_high: int,
    vulnerable_other: int,
    fan_in: int,
    cross_language: bool,
    tested: bool | None = None,
) -> ModuleRisk:
    """Score one module. `tested` overrides `facts.has_tests` when another module
    in the repository is known to test this one."""
    factors: list[dict] = []

    def add(factor: str, points: int, detail: str) -> None:
        if points > 0:
            factors.append({"factor": factor, "points": points, "detail": detail})

    add("size", _size_points(facts.loc), f"{facts.loc:,} lines of code across {facts.files} files.")

    runtime_points = {"eol": 15, "approaching": 8, "legacy": 5}.get(runtime_status.status, 0)
    add("runtime", runtime_points, runtime_status.note or f"Runtime status: {runtime_status.status}.")

    platform = min(25, 10 * len(facts.blockers))
    add("platform", platform, "; ".join(_BLOCKER_LABEL.get(b, b) for b in facts.blockers))

    add("dependencies", min(10, dependency_count // 3),
        f"{dependency_count} external packages, each needing a target-stack equivalent.")
    add("deprecated_dependencies", min(10, 5 * deprecated),
        f"{deprecated} deprecated package(s) must be replaced, not upgraded.")
    add("vulnerable_dependencies", min(10, 10 * vulnerable_high + 4 * vulnerable_other),
        f"{vulnerable_high} high/critical and {vulnerable_other} other known vulnerabilities.")
    add("coupling", min(10, 2 * fan_in),
        f"{fan_in} other module(s) depend on this one — a change here ripples.")

    is_tested = facts.has_tests if tested is None else tested
    add("tests", 0 if is_tested else 5,
        "No tests exercise this module, so preserved behaviour is harder to prove.")

    score = min(100, sum(f["points"] for f in factors))
    hard = any(b in HARD_BLOCKERS for b in facts.blockers)
    if hard or score >= 70:
        tier = "manual"
    elif not facts.blockers and score < 35 and not cross_language:
        tier = "mechanical"
    else:
        tier = "llm_assisted"
    return ModuleRisk(score=score, tier=tier, factors=factors)
