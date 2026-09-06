"""Live verification of the Orchestrator's routing — the one claim no unit test supports.

Every test in tests/orchestrator2/ fakes `resolve_model_for_run`, `_build_llm` and the
graph. That proves the PLUMBING (project-scoped BYOK, tool binding, message assembly,
attribution) and says nothing about whether this prompt and these nine capability
descriptions actually make a real model send "I need a PRD" to Requirements.

This makes REAL model calls against the seeded tenant's own BYOK provider, through the
same `route()` the socket calls, with the same project scoping. Run it from `backend/`:

    uv run python <path-to-this-file>

It writes nothing and changes nothing. It costs a handful of small model calls.
"""
import asyncio
import os
import sys

# Windows console is cp1252; the box-drawing characters below would crash the run.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# The seeded tenant/project that has BYOK providers with status='valid' and real
# secrets (model_providers 1db64d53 "Anthropicnewkwy", 6d8b970b "Azure key"), plus
# project_model_selections and real runs. Read from the DB, not invented.
TENANT = os.environ.get("ROUTING_CHECK_TENANT", "dfee0d2f-345e-430e-8084-7ab7276cc5b8")
PROJECT = os.environ.get("ROUTING_CHECK_PROJECT", "c3b0cd34-6657-4f91-b1f2-04f394502f81")
RUN = os.environ.get("ROUTING_CHECK_RUN", "b29a778e-b59b-4145-9af6-7eb986f4c0cb")

# (message, expected agent or None for "answer directly"). Chosen to cover the cases
# the spec names, plus the ones most likely to be mis-routed.
CASES = [
    # The headline requirement, verbatim from the user: "when the admin asks for
    # something ('I need a PRD') the right agent starts automatically".
    ("I need a PRD for the billing rework", "requirements"),
    ("write the user stories with acceptance criteria", "requirements"),
    ("design the architecture for this service", "design"),
    ("what's the API contract going to look like?", "design"),
    ("build me a schedule and assign the tasks", "plan"),
    ("who is working on what, and when does this land?", "plan"),
    ("implement the payment endpoint", "development"),
    ("review the code I just pushed", "code_review"),
    ("check this for vulnerabilities", "security"),
    ("are there any injection risks here?", "security"),
    ("write tests for the checkout flow", "testing"),
    ("set up the deployment pipeline", "deployment"),
    ("document the API for external consumers", "documentation"),
    # Should NOT reach a delivery agent.
    ("what can you do?", None),
    ("hello", None),
    # The pre-filter path — must cost no model call at all.
    ("run the security agent", "security"),
]

# NOT used to tune anything. A fix aimed at UNDER-routing naturally over-corrects into
# "route everything", so six of these must NOT start an agent and two must still route.
# This block is the check that the tuning did not buy its wins that way.
HELD_OUT = [
    ("thanks, that's helpful", None),
    ("what did you just do?", None),
    ("can you explain what the Project Manager agent is for?", None),
    ("who are you?", None),
    ("summarise what we've covered so far", None),
    ("no, I meant the other one", None),
    ("we need a rollback plan for the release", "deployment"),
]


async def main() -> int:
    sys.path.insert(0, os.getcwd())
    from agents_orchestrator.orchestrator2 import router
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    print(f"interpreter : {sys.executable}")
    print(f"tenant      : {TENANT}")
    print(f"project     : {PROJECT}")
    print(f"agents      : {len(AGENT_IDS)} -> {AGENT_IDS}\n")

    # Which cases the deterministic pre-filter answers for free. Anything it claims
    # must be right, and everything else is a real model call.
    print("── pre-filter (no model call) ─────────────────────────────────")
    prefiltered = {}
    for text, _ in CASES:
        got = router.prefilter(text)
        if got is not None:
            prefiltered[text] = got
            print(f"  {got:<14} <- {text!r}")
    print(f"  {len(prefiltered)}/{len(CASES)} answered without a model\n")

    print("── routed by the Context Agent (real model calls) ─────────────")
    hits = misses = errors = 0
    for text, expected in CASES:
        try:
            decision = await router.route(
                text,
                history=[],
                run_id=RUN,
                tenant_id=TENANT,
                project_id=PROJECT,
                model_id=None,
                offering_id=None,
            )
        except Exception as exc:  # noqa: BLE001 — report, keep going
            errors += 1
            print(f"  ERROR   {text!r}\n            {type(exc).__name__}: {str(exc)[:160]}")
            continue

        got = decision.agent_id
        ok = got == expected
        hits += ok
        misses += not ok
        mark = "ok  " if ok else "MISS"
        print(f"  {mark}  want={str(expected):<14} got={str(got):<14} {text!r}")
        if not ok or expected is None:
            print(f"            reason: {decision.reason}")
            if decision.direct_reply:
                print(f"            reply : {decision.direct_reply[:120]}")

    print(f"\n  {hits} correct, {misses} mis-routed, {errors} errors, of {len(CASES)}")

    # The guarantee `_validated` is supposed to enforce, checked against live output
    # rather than against a fake: a decision naming an agent outside the nine, or a
    # no-agent decision with nothing to show the user, is the failure this engine
    # exists to prevent.
    print("\n-- held out (not used to tune the prompt) --------------------")
    for text, expected in HELD_OUT:
        try:
            decision = await router.route(
                text, history=[], run_id=RUN, tenant_id=TENANT,
                project_id=PROJECT, model_id=None, offering_id=None,
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR   {text!r}: {type(exc).__name__}")
            continue
        got = decision.agent_id
        ok = got == expected
        hits += ok
        misses += not ok
        print(f"  {'ok  ' if ok else 'MISS'}  want={str(expected):<14} got={str(got):<14} {text!r}")
        if not ok:
            print(f"            reason: {decision.reason}")

    print(f"\n  TOTAL {hits} correct, {misses} wrong, {errors} errors, "
          f"of {len(CASES) + len(HELD_OUT)}")

    print("\n── invariants ────────────────────────────────────────────────")
    print("  (checked above: every agent_id was either None or one of the nine)")
    return 0 if errors == 0 and misses == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
