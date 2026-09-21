"""Running an API suite against the running application.

Cases run IN ORDER, because they depend on each other: a case captures values from its JSON
response (`capture: {"code": "shortCode"}`) and later cases use them (`/api/links/{{code}}`).
When the case that should have captured a value failed, the cases that need it are "Not run"
with that reason — sending a literal "{{code}}" would fail for a reason that is not the app's.

An application that does not answer at all is refused before any case runs: every case
"failing" with "connection refused" is not a test result.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin

import httpx

from agents_orchestrator.testing_agent.suites.reports import ResultRow

Report = Callable[[str], Awaitable[None]]
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
TIMEOUT_S = 20.0
EXCERPT = 4000


def substitute(text: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """`text` with {{variables}} filled, and the names that had no value."""
    missing: list[str] = []

    def fill(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return str(variables[name])
        missing.append(name)
        return m.group(0)

    return _VAR.sub(fill, text or ""), missing


def dig(data: Any, path: str) -> Any:
    """`data` at a dot path (`data.items.0.id`); raises KeyError when it is not there."""
    cur = data
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


async def check_reachable(client: httpx.AsyncClient, base_url: str) -> None:
    try:
        await client.get(base_url, timeout=10.0)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"The application at {base_url} did not respond ({type(exc).__name__}). Start it, "
                           "check the URL, and run again.") from exc


async def execute(cases: list[Any], base_url: str, report: Report) -> tuple[list[ResultRow], list[list[Any]]]:
    """(result rows in order, response rows for the report's Responses sheet)."""
    base = base_url.rstrip("/") + "/"
    rows: list[ResultRow] = []
    responses: list[list[Any]] = []
    variables: dict[str, str] = {}
    captured_by: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as client:
        await check_reachable(client, base_url)
        for case in cases:
            subject = f"{case.method} {case.path}"
            planned = set(_VAR.findall(" ".join([case.path, case.body, *case.headers.values()])))
            unset = sorted(v for v in planned if v not in variables)
            if unset:
                who = ", ".join(sorted({captured_by.get(v, "an earlier case") for v in unset}))
                rows.append(ResultRow(case.id, case.title, subject, "Not run", None,
                                      f"Needs {', '.join('{{' + v + '}}' for v in unset)}, which {who} did not capture."))
                continue

            path, _ = substitute(case.path, variables)
            body, _ = substitute(case.body, variables)
            headers = {k: substitute(v, variables)[0] for k, v in case.headers.items()}
            url = path if path.startswith(("http://", "https://")) else urljoin(base, path.lstrip("/"))
            kwargs: dict[str, Any] = {"headers": headers}
            if body:
                try:
                    kwargs["json"] = json.loads(body)
                except json.JSONDecodeError:
                    kwargs["content"] = body.encode("utf-8")
            await report(f"{case.id}: {case.method} {path}")
            started = time.monotonic()
            try:
                resp = await client.request(case.method, url, **kwargs)
            except httpx.HTTPError as exc:
                responses.append([case.id, f"{case.method} {url}", "—", ""])
                try:
                    await check_reachable(client, base_url)
                except RuntimeError:
                    # THE APP WENT DOWN, not seven separate failures. A live run showed the first
                    # request dropped and every later one "connection refused": say it once, on the
                    # case it happened during, and do not pretend the rest were tested.
                    rows.append(ResultRow(case.id, case.title, subject, "Error", None,
                                          f"The application stopped responding during this request "
                                          f"({type(exc).__name__}) — it may have crashed. Check its console."))
                    stopped_at = case.id
                    for later in cases[cases.index(case) + 1:]:
                        rows.append(ResultRow(later.id, later.title, f"{later.method} {later.path}", "Not run", None,
                                              f"Not run: the application stopped responding during {stopped_at}."))
                    await report(f"The application stopped responding during {stopped_at} — the remaining cases were not run")
                    return rows, responses
                rows.append(ResultRow(case.id, case.title, subject, "Error", None,
                                      f"The request could not be completed: {type(exc).__name__}: {exc}"))
                continue
            ms = int((time.monotonic() - started) * 1000)
            text = resp.text or ""
            responses.append([case.id, f"{case.method} {url}", resp.status_code, text[:EXCERPT]])

            problems = []
            if resp.status_code != case.expected_status:
                problems.append(f"Expected status {case.expected_status}, got {resp.status_code}.")
            missing = [s for s in case.expected_body_contains if s not in text]
            if missing:
                problems.append("The response did not contain: " + "; ".join(f'"{s}"' for s in missing) + ".")

            notes = []
            if case.capture:
                try:
                    data = resp.json()
                except ValueError:
                    data = None
                for name, where in case.capture.items():
                    try:
                        variables[name] = str(dig(data, where))
                        captured_by[name] = case.id
                    except (KeyError, TypeError):
                        captured_by.setdefault(name, case.id)
                        notes.append(f"Could not capture {{{{{name}}}}} from `{where}` in the response.")

            status = "Failed" if problems else "Passed"
            message = " ".join(problems + notes)
            if problems:
                message += f" Response: {text[:300]}"
            rows.append(ResultRow(case.id, case.title, subject, status, ms, message.strip(), f"HTTP {resp.status_code}"))
    return rows, responses
