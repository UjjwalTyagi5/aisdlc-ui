"""Every agent, end to end, against the real stack — and what each one DEPOSITED.

WHY THIS EXISTS. The Development agent was tested by hand and three defects fell out
of that one agent alone: a redundant routing ping-pong, an empty code tree, and chat
messages filed as deliverables. Eight agents had never been exercised the same way.
This runs all nine plus the router, through the real graph, the real BYOK provider and
the real database, and then asks the question the unit tests cannot:

    did this agent produce something, and is what landed in Deliverables a DOCUMENT?

Three distinct failures it is built to separate, because they look identical in the UI
(an empty Deliverables tab) and have nothing to do with each other:

  * the agent errored          -> `error` event, no text at all
  * the agent replied in chat  -> text, but no deliverable, correctly
  * the agent wrote a document -> text, and a deliverable row

Every prompt below asks for a DELIVERABLE outright ("write the PRD", not "what is a
PRD"), so for these prompts the third outcome is the expected one. An agent that
answers conversationally here is a finding, not a pass.

REAL MONEY. Nine graph runs against the tenant's own provider. Run it deliberately.

Run from `backend/`:  ./.venv/Scripts/python.exe scripts/live_all_agents_check.py
                      ./.venv/Scripts/python.exe scripts/live_all_agents_check.py design security
"""
import asyncio
import io
import os
import sys
import time
import uuid

# The box codes below render as `?` under the Windows console's cp1252 default, and
# `print` RAISES rather than degrading. Reconfiguring is cheaper than avoiding them.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The seeded dev tenant, and the project the user tests in — `reall`, the one whose
# `connectors` map binds azure_devops to development, design, requirements and
# code_review. Same constants as the other live checks in this directory.
TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "c3b0cd34-6657-4f91-b1f2-04f394502f81"
# sarthakk2004@gmail.com. A REAL user row, because `user_id` selects this person's own
# project-scoped connector credential — a made-up uuid would resolve no credential and
# the Development agent would fail for a reason that has nothing to do with the code.
USER = "af57932d-f4f2-4673-aef7-d33b75c022f4"

#: One prompt per agent, each asking for that agent's actual work product. Written to
#: be answerable without a repository or a running service, because an agent that
#: cannot get started tells us nothing about whether its OUTPUT is captured — which is
#: what this script is measuring. Development is the exception and stays a code task,
#: since its clone is the thing the code tree is built from.
PROMPTS = {
    "requirements": (
        "Write the PRD for a coffee-ordering web app: customers browse a menu, "
        "customise a drink, pay, and collect in store. Include goals, user stories "
        "and acceptance criteria. Produce the document now — do not ask me questions."
    ),
    "design": (
        "Write the high-level design and the database schema for that coffee-ordering "
        "app. Include the component breakdown and the main tables with their columns. "
        "Produce the document now — do not ask me questions."
    ),
    "plan": (
        "Write the delivery plan for that coffee-ordering app: the milestones, the "
        "work items under each, and a rough sequence. Produce the document now — do "
        "not ask me questions."
    ),
    "development": (
        "Write the Python module for the order-total calculation in that coffee app: "
        "base price, size modifier, extras, and tax. Include the code and a short "
        "explanation. Produce it now — do not ask me questions."
    ),
    "testing": (
        "Write the test plan for that order-total calculation: the cases, the expected "
        "results, and what is deliberately not covered. Produce the document now — do "
        "not ask me questions."
    ),
    "code_review": (
        "Review this function and write the review:\n\n"
        "```python\n"
        "def total(items, tax):\n"
        "    t = 0\n"
        "    for i in items:\n"
        "        t = t + i['price'] * i['qty']\n"
        "    return t + t * tax\n"
        "```\n"
        "Cover correctness, rounding, and input validation. Produce the document now."
    ),
    "security": (
        "Write the security review for that coffee-ordering app: the threats against "
        "payment and account handling, and the mitigations. Produce the document now "
        "— do not ask me questions."
    ),
    "deployment": (
        "Write the deployment plan for that coffee-ordering app on Azure: the "
        "environments, the pipeline stages, and the rollback approach. Produce the "
        "document now — do not ask me questions."
    ),
    "documentation": (
        "Write the README for that coffee-ordering app: what it is, how to run it "
        "locally, and how to configure it. Produce the document now — do not ask me "
        "questions."
    ),
}

#: A turn that produces nothing for this long is reported as a hang rather than left
#: to block the run. Generous: some of these agents call tools before they say a word.
TURN_TIMEOUT_S = 420

#: Where each agent's full reply and captured deliverables are written, set from
#: `--dump DIR`. Reading them is the only way to tell a real document from a plausible
#: one, and the summary table cannot do it.
DUMP_DIR = None


class Result:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.text = ""
        self.error = None
        self.tool_calls = 0
        self.seconds = 0.0
        self.deliverables = []
        self.selected_reason = None

    @property
    def verdict(self):
        if self.error:
            return "ERROR"
        if not self.text.strip():
            return "SILENT"
        if not self.deliverables:
            return "CHAT-ONLY"
        return "ok"


async def run_one(agent_id: str, text: str) -> Result:
    """One agent, one turn, through the same entry point the socket uses."""
    from agents_orchestrator.orchestrator2 import deliverables as dv
    from agents_orchestrator.orchestrator2.dispatch import run_agent

    import shared.db as shared_db
    from shared.models.orm import Run

    result = Result(agent_id)
    run_id = str(uuid.uuid4())

    # A real run row: deliverables carry a foreign key to it, and a turn whose capture
    # silently failed on a missing FK would look exactly like an agent that produced
    # nothing — the confusion this script exists to remove.
    async with shared_db.get_db_session_for_tenant(TENANT) as s:
        s.add(Run(id=uuid.UUID(run_id), project_id=uuid.UUID(PROJECT),
                  tenant_id=uuid.UUID(TENANT), stage=agent_id, status="running"))
        await s.commit()

    started = time.monotonic()
    try:
        async def _drive():
            async for event in run_agent(
                agent_id,
                text=text,
                run_id=run_id,
                tenant_id=TENANT,
                model_id=None,
                offering_id=None,
                project_id=PROJECT,
                user_id=USER,
                context="",
                reason="live check",
            ):
                kind = event.get("type")
                if kind == "stream_chunk":
                    result.text += str(event.get("content") or "")
                elif kind == "tool.call":
                    result.tool_calls += 1
                elif kind == "agent.selected":
                    result.selected_reason = event.get("reason")
                elif kind == "error":
                    result.error = str(event.get("message") or event.get("detail") or "?")

        await asyncio.wait_for(_drive(), timeout=TURN_TIMEOUT_S)
    except asyncio.TimeoutError:
        result.error = f"no completion within {TURN_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001 — one agent's failure must not end the run
        result.error = f"{type(exc).__name__}: {exc}"
    result.seconds = time.monotonic() - started

    # READ BACK FROM THE DATABASE, not from what capture returned. The question is
    # what the Deliverables tab will show, and the tab reads this table.
    try:
        result.deliverables = await dv.deliverables_for_run(run_id, TENANT)
    except Exception as exc:  # noqa: BLE001
        result.error = (result.error or "") + f" | read-back failed: {exc}"

    # The summary table says WHETHER a document landed. Only the text says whether it
    # is the RIGHT document — the Security agent's first run produced a deliverable
    # titled "What I Can Do Instead", which the table scored as a pass.
    if DUMP_DIR:
        os.makedirs(DUMP_DIR, exist_ok=True)
        with io.open(os.path.join(DUMP_DIR, f"{agent_id}.md"), "w",
                     encoding="utf-8") as fh:
            parts = [
                f"# {agent_id} — {result.verdict} — {result.seconds:.0f}s, "
                f"{result.tool_calls} tool calls",
                "## reply",
                result.text,
            ]
            for d in result.deliverables:
                parts.append(f"## deliverable: {d['title']} [{d['kind']}]")
                parts.append(d["content"] or "")
            if result.error:
                parts.append("## error")
                parts.append(result.error)
            fh.write("\n\n".join(parts) + "\n")
    return result


def report(results):
    print("\n" + "=" * 78)
    print("  agent            verdict     secs  tools  deliverables  chars")
    print("=" * 78)
    for r in results:
        print(f"  {r.agent_id:<15} {r.verdict:<10} {r.seconds:5.0f}  {r.tool_calls:5}"
              f"  {len(r.deliverables):12}  {len(r.text):5}")
        for d in r.deliverables:
            print(f"        - [{d['kind']}] {d['title']}  ({len(d['content'] or '')} chars)")
        if r.error:
            print(f"        ! {r.error[:200]}")

    bad = [r for r in results if r.verdict != "ok"]
    print("=" * 78)
    if not bad:
        print(f"  all {len(results)} agents produced a document")
        return 0
    print(f"  {len(bad)} of {len(results)} did not produce a document:")
    for r in bad:
        print(f"    {r.verdict:<10} {r.agent_id}")
        if r.verdict == "CHAT-ONLY":
            print(f"               first 240 chars: {r.text.strip()[:240]!r}")
    return 1


async def main() -> int:
    sys.path.insert(0, os.getcwd())
    print("interpreter :", sys.executable)
    print("tenant      :", TENANT)
    print("project     :", PROJECT)

    global DUMP_DIR
    if "--dump" in sys.argv:
        DUMP_DIR = sys.argv[sys.argv.index("--dump") + 1]
        print("dumping to :", DUMP_DIR)

    wanted = [a for a in sys.argv[1:]
              if not a.startswith("-") and a != DUMP_DIR]
    agents = [a for a in PROMPTS if not wanted or a in wanted]
    unknown = [a for a in wanted if a not in PROMPTS]
    if unknown:
        print("unknown agent(s):", ", ".join(unknown))
        return 2
    print("agents      :", ", ".join(agents), "\n")

    results = []
    for agent_id in agents:
        print(f"── {agent_id} " + "─" * (60 - len(agent_id)))
        result = await run_one(agent_id, PROMPTS[agent_id])
        results.append(result)
        head = result.text.strip().splitlines()[:1]
        print(f"   {result.verdict}  {result.seconds:.0f}s  "
              f"{len(result.deliverables)} deliverable(s)  "
              f"{head[0][:70] if head else '(no text)'}")
    return report(results)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
