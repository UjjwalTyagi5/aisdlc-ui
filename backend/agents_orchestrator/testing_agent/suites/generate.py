"""Writing the three suites with the run's model.

One call per suite type, concurrently. Each returns JSON, is validated case by case
(`models.validate_cases`), and gets ONE repair round when cases were rejected or the JSON
did not parse — with the problems named, so the model fixes those cases rather than
starting again. A suite with no valid case after that is an error for that type, stated;
the other types still file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

from agents_orchestrator.testing_agent.suites.context import RepoContext
from agents_orchestrator.testing_agent.suites.models import KIND_LABEL, KIND_PREFIX, validate_cases

logger = logging.getLogger(__name__)

Report = Callable[[str], Awaitable[None]]

_SYSTEM = (
    "You are a senior QA engineer writing test cases for an enterprise delivery team. You write "
    "precise, runnable cases grounded ONLY in the code and approved documents you are given. Never "
    "invent a page, button, field, message, route or function that is not in the material. Answer "
    "with ONE JSON object and nothing else."
)

_GUIDE = {
    "unit": """Write UNIT test cases: one function, method or route handler tested in isolation.
Cover every exported service, model or controller function in the code shown. For each, include a
happy path, error cases (invalid or missing input, dependency failure) and edge cases (boundaries,
empty values). Aim for 12–25 cases.

Each case:
  {"id": "UT-001", "title": "...", "module": "<repo-relative file path from the code shown>",
   "function": "<exact function / method name>", "scenario": "Happy path" | "Error" | "Edge",
   "input": "<concrete input values>", "expected": "<observable result: return value, thrown error, DB change>",
   "priority": "High" | "Medium" | "Low", "requirement": "<BRD requirement id or name it verifies, or empty>"}""",

    "functional": """Write FUNCTIONAL test cases: end-to-end journeys a tester runs in a web browser against the
running application, exactly as a user would. Cover the main user journeys in the approved requirements
that the views/templates implement, plus validation errors and not-found paths. Aim for 6–12 cases.

Each case:
  {"id": "FT-001", "title": "...", "requirement": "<BRD requirement>", "priority": "High" | "Medium" | "Low",
   "preconditions": "<data or state needed first, or empty>",
   "steps": [ {"action": "...", "target": "...", "value": "..."} ],
   "expected": "<what the user sees at the end>"}

Step actions (use ONLY these):
  open            target = a path on the app, starting with "/" (e.g. "/", "/links")
  click           target = the VISIBLE text of the button or link, exactly as in the template
  type            target = the field's label, placeholder or name attribute; value = the text to enter
  select          target = the field's label or name; value = the option's visible text
  check           target = the checkbox's label or name
  wait            value = seconds
  assert_text     value = text that must be visible on the page, copied exactly from the template
  assert_not_text value = text that must NOT be visible
  assert_url      value = a fragment the address must contain
Every case starts with an "open" step and ENDS with at least one assert_text or assert_url step that
verifies its expected result — a case without a check is rejected. Assert text that the page really
shows after the action: a heading, a table value, or a message the route renders or redirects with,
copied exactly from the views and routes shown. Use only labels, texts and paths that appear there. Use realistic test data (e.g. "https://intranet.example.com/news/2026/autumn").""",

    "api": """Write API test cases for the HTTP endpoints the routes define — requests a tester sends to the
running application. Cover every endpoint: successful calls, validation errors (400/422), not-found
(404) and method/format errors where the code handles them. Aim for 8–15 cases, ordered so data a
later case needs is created by an earlier one.

Each case:
  {"id": "AT-001", "title": "...", "requirement": "<BRD requirement>", "priority": "High" | "Medium" | "Low",
   "method": "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
   "path": "<path exactly as routed, starting with '/', with concrete values>",
   "headers": {"Content-Type": "application/json"},
   "body": <JSON object for the request body, or "">,
   "expected_status": <integer the code returns>,
   "expected_body_contains": ["<text that will literally appear in the response body>"],
   "capture": {"<variable>": "<dot path into the JSON response, e.g. code or data.id>"}}

To use a value created earlier, capture it in the creating case and write {{variable}} in a later
path or body (e.g. "/api/links/{{code}}"). Only use status codes and response fields the code shown
actually produces.""",
}


def _extract_json(text: str) -> Any:
    """The JSON object in a model's answer — fenced or not. Raises ValueError."""
    t = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    start = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON in the answer")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(t[start:], start=start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("the JSON in the answer is incomplete")


def _cases_of(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("cases", "test_cases", "tests"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError("the JSON has no `cases` list")


def build_prompt(kind: str, *, repo: RepoContext, documents_text: str, project: str) -> str:
    docs = documents_text.strip()[:25_000] if documents_text else ""
    return "\n\n".join(p for p in (
        f"PROJECT: {project}",
        _GUIDE[kind],
        'Return exactly: {"cases": [ ... ]}',
        f"APPLICATION: {repo.app_notes or 'not detected'}",
        ("APPROVED PROJECT DOCUMENTS (the requirements the cases verify):\n" + docs) if docs
        else "APPROVED PROJECT DOCUMENTS: none on file — derive the behaviour from the code.",
        "SOURCE FILES ON THE BRANCH:\n" + repo.inventory(),
        "CODE:\n" + repo.text,
    ) if p)


async def _ask(llm, messages) -> str:
    from langchain_core.messages import AIMessage  # noqa: PLC0415

    reply = await llm.ainvoke(messages)
    content = reply.content if isinstance(reply, AIMessage) or hasattr(reply, "content") else reply
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content or "")


async def generate_suite(kind: str, llm, *, repo: RepoContext, documents_text: str, project: str,
                         report: Report) -> tuple[list[Any], list[str]]:
    """(cases, problems) for one suite type. Raises RuntimeError when no valid case came back."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: PLC0415

    label = KIND_LABEL[kind]
    messages = [SystemMessage(content=_SYSTEM),
                HumanMessage(content=build_prompt(kind, repo=repo, documents_text=documents_text, project=project))]
    answer = await _ask(llm, messages)
    problems: list[str] = []
    cases: list[Any] = []
    try:
        cases, problems = validate_cases(kind, _cases_of(_extract_json(answer)))
    except (ValueError, json.JSONDecodeError) as exc:
        problems = [f"the answer was not usable JSON ({exc})"]

    if problems:
        await report(f"{label}: {len(cases)} valid case(s), {len(problems)} to fix — asking the model to correct them")
        messages += [AIMessage(content=answer[:20_000]), HumanMessage(content=(
            "Some cases could not be used:\n- " + "\n- ".join(problems[:30]) +
            "\n\nReturn the COMPLETE corrected suite as {\"cases\": [...]} — every case, valid ones included, "
            "following the field rules exactly."))]
        answer = await _ask(llm, messages)
        try:
            fixed, fixed_problems = validate_cases(kind, _cases_of(_extract_json(answer)))
            if len(fixed) >= len(cases):
                cases, problems = fixed, fixed_problems
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"the corrected answer was not usable JSON ({exc})")

    if not cases:
        raise RuntimeError(f"no usable {label.lower()} test case was produced: " + "; ".join(problems[:5]))
    # Stable, readable ids in order.
    cases = [c.model_copy(update={"id": f"{KIND_PREFIX[kind]}-{i:03d}"}) for i, c in enumerate(cases, start=1)]
    return cases, problems


async def generate_suites(kinds: list[str], llm, *, repo: RepoContext, documents_text: str, project: str,
                          report: Report) -> dict[str, dict]:
    """{kind: {"cases": [...], "problems": [...]} | {"error": "..."}} for every requested kind."""

    async def one(kind: str) -> tuple[str, dict]:
        await report(f"Writing {KIND_LABEL[kind].lower()} test cases")
        try:
            cases, problems = await generate_suite(kind, llm, repo=repo, documents_text=documents_text,
                                                   project=project, report=report)
            await report(f"{KIND_LABEL[kind]}: {len(cases)} test case(s) written")
            return kind, {"cases": cases, "problems": problems}
        except Exception as exc:  # noqa: BLE001 — the other suites still file; this one says why
            logger.exception("suite generation failed for %s", kind)
            from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

            module = (type(exc).__module__ or "").split(".")[0]
            reason = friendly_model_error(exc) if module in ("litellm", "openai", "anthropic", "httpx") else str(exc)
            await report(f"{KIND_LABEL[kind]}: failed — {reason}")
            return kind, {"error": reason}

    return dict(await asyncio.gather(*(one(k) for k in kinds)))
