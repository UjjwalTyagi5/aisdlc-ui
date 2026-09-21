"""The three kinds of test case, as data.

Validation is lenient on spelling and strict on meaning: a model that writes "POST " or
"Happy path" is normalised, but a functional step with an unknown action, an API case with
no path, or a unit case with nothing to test is rejected — a case that cannot be run or
understood is worse than one fewer case.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SuiteKind = Literal["unit", "functional", "api"]
KINDS: tuple[str, ...] = ("unit", "functional", "api")
KIND_LABEL = {"unit": "Unit", "functional": "Functional", "api": "API"}
KIND_PREFIX = {"unit": "UT", "functional": "FT", "api": "AT"}

Priority = Literal["High", "Medium", "Low"]
_PRIORITY = {"high": "High", "medium": "Medium", "med": "Medium", "low": "Low", "p1": "High", "p2": "Medium", "p3": "Low"}
_SCENARIO = {"happy": "Happy path", "happy path": "Happy path", "positive": "Happy path",
             "error": "Error", "negative": "Error", "error case": "Error",
             "edge": "Edge", "edge case": "Edge", "boundary": "Edge"}

#: What a functional step can do. `target` is what a person sees: a button or link's text,
#: a field's label or placeholder — or `css:<selector>` when there is nothing visible.
STEP_ACTIONS = ("open", "click", "type", "select", "check", "wait", "assert_text", "assert_not_text", "assert_url")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _priority(v: Any) -> str:
    return _PRIORITY.get(_text(v).lower(), "Medium")


class _Case(BaseModel):
    id: str
    title: str
    requirement: str = ""
    priority: Priority = "Medium"

    @field_validator("id", "title", "requirement", mode="before")
    @classmethod
    def _strip(cls, v):
        return _text(v)

    @field_validator("priority", mode="before")
    @classmethod
    def _prio(cls, v):
        return _priority(v)


class UnitCase(_Case):
    module: str                       # the file under test, repo-relative
    function: str = ""                # the function / method / route handler
    scenario: str = "Happy path"      # Happy path | Error | Edge
    input: str = ""
    expected: str

    @field_validator("module", "function", "input", "expected", mode="before")
    @classmethod
    def _strip_fields(cls, v):
        return _text(v)

    @field_validator("scenario", mode="before")
    @classmethod
    def _scenario(cls, v):
        return _SCENARIO.get(_text(v).lower(), _text(v) or "Happy path")

    @model_validator(mode="after")
    def _meaningful(self):
        if not (self.module and self.expected):
            raise ValueError("a unit case needs the module under test and an expected result")
        return self


class FunctionalStep(BaseModel):
    action: Literal["open", "click", "type", "select", "check", "wait", "assert_text", "assert_not_text", "assert_url"]
    target: str = ""
    value: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        a = re.sub(r"[\s-]+", "_", _text(v).lower())
        return {"navigate": "open", "goto": "open", "go_to": "open", "visit": "open", "fill": "type",
                "enter": "type", "input": "type", "press": "click", "tap": "click", "choose": "select",
                "verify_text": "assert_text", "expect_text": "assert_text", "see": "assert_text",
                "assert_visible": "assert_text", "verify_url": "assert_url", "expect_url": "assert_url",
                "tick": "check", "sleep": "wait", "pause": "wait"}.get(a, a)

    @field_validator("target", "value", mode="before")
    @classmethod
    def _strip(cls, v):
        return _text(v)

    @model_validator(mode="after")
    def _complete(self):
        needs_target = {"click", "type", "select", "check"}
        needs_value = {"type", "select", "assert_text", "assert_not_text", "assert_url"}
        if self.action in needs_target and not self.target:
            raise ValueError(f"a '{self.action}' step needs a target")
        if self.action in needs_value and not self.value:
            raise ValueError(f"a '{self.action}' step needs a value")
        return self

    def describe(self) -> str:
        return {
            "open": f"Open {self.target or '/'}",
            "click": f"Click \"{self.target}\"",
            "type": f"Type \"{self.value}\" into \"{self.target}\"",
            "select": f"Select \"{self.value}\" in \"{self.target}\"",
            "check": f"Tick \"{self.target}\"",
            "wait": f"Wait {self.value or '1'} s",
            "assert_text": f"Check the page shows \"{self.value}\"",
            "assert_not_text": f"Check the page does not show \"{self.value}\"",
            "assert_url": f"Check the address contains \"{self.value}\"",
        }[self.action]


class FunctionalCase(_Case):
    preconditions: str = ""
    steps: list[FunctionalStep] = Field(min_length=1)
    expected: str

    @field_validator("preconditions", "expected", mode="before")
    @classmethod
    def _strip_fields(cls, v):
        return _text(v)

    @model_validator(mode="after")
    def _checks_something(self):
        # A LIVE SUITE HAD NINE CASES AND NO CHECK: open, type, click — and the expected result
        # only in prose. Run in a browser, every one of them "passes". A case must verify.
        if not any(s.action.startswith("assert_") for s in self.steps):
            raise ValueError("a functional case must end with an assert_text or assert_url step that checks the expected result")
        return self


class ApiCase(_Case):
    method: str
    path: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""                     # JSON text, or empty
    expected_status: int
    expected_body_contains: list[str] = Field(default_factory=list)
    #: {"variable": "json.path"} — saved from THIS response for later cases' `{{variable}}`.
    capture: dict[str, str] = Field(default_factory=dict)

    @field_validator("method", mode="before")
    @classmethod
    def _method(cls, v):
        m = _text(v).upper()
        if m not in HTTP_METHODS:
            raise ValueError(f"unknown HTTP method {v!r}")
        return m

    @field_validator("path", mode="before")
    @classmethod
    def _path(cls, v):
        p = _text(v)
        if not p:
            raise ValueError("an API case needs a path")
        return p if p.startswith(("/", "http://", "https://")) else f"/{p}"

    @field_validator("body", mode="before")
    @classmethod
    def _body(cls, v):
        return _text(v)

    @field_validator("headers", "capture", mode="before")
    @classmethod
    def _mapping(cls, v):
        if v in (None, ""):
            return {}
        if isinstance(v, str):
            v = v.strip()
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pairs = [p.split("=", 1) for p in re.split(r"[;\n]", v) if "=" in p]
                v = {k.strip(): val.strip() for k, val in pairs}
        return {str(k): _text(val) for k, val in dict(v).items()}

    @field_validator("expected_status", mode="before")
    @classmethod
    def _status(cls, v):
        m = re.search(r"\d{3}", _text(v))
        if not m:
            raise ValueError("an API case needs an expected HTTP status")
        return int(m.group(0))

    @field_validator("expected_body_contains", mode="before")
    @classmethod
    def _contains(cls, v):
        if v in (None, ""):
            return []
        if isinstance(v, str):
            return [s.strip() for s in re.split(r"\s*(?:\n|;|\|)\s*", v) if s.strip()]
        return [_text(s) for s in v if _text(s)]


CASE_MODEL: dict[str, type[_Case]] = {"unit": UnitCase, "functional": FunctionalCase, "api": ApiCase}


class SuiteMeta(BaseModel):
    kind: SuiteKind
    project: str = ""
    source_project: str = ""           # the ADO project / GitHub owner the repository lives under
    repository: str = ""
    branch: str = ""
    commit: str = ""
    generated_at: str = ""
    sources: str = ""                  # what the cases were derived from
    app_notes: str = ""                # e.g. how the app starts, its port


def _reason(exc: Exception) -> str:
    """What was wrong, in words — pydantic's messages, without its field paths and footer."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            msgs = []
            for e in errors():
                where = ".".join(str(p) for p in e.get("loc", ()) if not isinstance(p, int))
                msg = str(e.get("msg", "")).removeprefix("Value error, ")
                msgs.append(f"{where}: {msg}" if where and msg and where not in msg else msg)
            return "; ".join(m for m in msgs if m) or type(exc).__name__
        except Exception:  # noqa: BLE001
            pass
    return str(exc) or type(exc).__name__


def validate_cases(kind: str, raw: list[Any]) -> tuple[list[_Case], list[str]]:
    """(valid cases, problems) — ids made unique and prefixed, invalid cases reported by id."""
    model = CASE_MODEL[kind]
    cases: list[_Case] = []
    problems: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw or [], start=1):
        if not isinstance(item, dict):
            problems.append(f"case {i}: not an object")
            continue
        try:
            case = model.model_validate(item)
        except Exception as exc:  # noqa: BLE001 — pydantic's message names the field
            problems.append(f"{item.get('id') or f'case {i}'}: {_reason(exc)}")
            continue
        cid = case.id or f"{KIND_PREFIX[kind]}-{i:03d}"
        if cid in seen:
            cid = f"{cid}-{i}"
        seen.add(cid)
        cases.append(case.model_copy(update={"id": cid}))
    if kind == "api":
        cases, problems = _captured_before_use(cases, problems)
    return cases, problems


_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _captured_before_use(cases: list[Any], problems: list[str]) -> tuple[list[Any], list[str]]:
    """An API case may only use a `{{variable}}` an EARLIER case captures — run in order, anything
    else sends a literal "{{code}}" to the server and fails for a reason that is not the app's."""
    captured: set[str] = set()
    kept: list[Any] = []
    for case in cases:
        used = set(_VAR.findall(" ".join([case.path, case.body, *case.headers.values()])))
        missing = sorted(used - captured)
        if missing:
            problems.append(f"{case.id}: uses {', '.join('{{' + m + '}}' for m in missing)} before any earlier case captures it")
            continue
        captured |= set(case.capture)
        kept.append(case)
    return kept, problems
