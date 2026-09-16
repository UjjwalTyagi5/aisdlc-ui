"""The Testing agent's test case document, painted with the platform's identity.

WHY A WORD DOCUMENT BESIDE THE SPREADSHEET. The test plan the agent generates was
written out as a bare pandas spreadsheet (`test_plan.xlsx`) and nothing else — seven
columns, no title, no context. The Requirements agent's BRD and the Design agent's
document arrive as designed documents with a title band, a key-facts strip and the
brief's palette; a tester putting a test case document forward for a Project Admin's
approval, beside those, needs the same. The spreadsheet stays (it is the working
copy); this is the record.

WHAT IT HOLDS. One numbered section per scenario type — Happy Path, Error Case, Edge
Case — each a table of the cases: id, what is tested, summary, data, steps, expected
result. When a run has produced results, a final section lists each executed test and
its outcome as a pill (Pass green, Fail red, Blocked amber). The facts strip says what
the cases were derived from — the project's approved BRD / design, an upload, or the
code — and how many there are.

Pure: builds markdown from the plan and paints it with `shared/docs/markdown_docx`.
No I/O beyond writing the file; safe to call from a graph node.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from shared.docs.markdown_docx import render_markdown_docx

SCENARIO_ORDER = ("Happy Path", "Error Case", "Edge Case")
SECTION_TITLE = {"Happy Path": "HAPPY PATH", "Error Case": "ERROR CASES", "Edge Case": "EDGE CASES"}
_STEP_RE = re.compile(r"\s*(?<!\d)(\d{1,2})[.)]\s+")


@dataclass
class TestDocMeta:
    title: str
    project: str = ""
    source: str = ""
    generated_on: str = ""
    status: str = "Awaiting approval"
    test_types: list[str] = field(default_factory=list)


def _cell(text: Any) -> str:
    """One table cell: no pipes (they split the row), no newlines (they end it)."""
    s = str(text if text is not None else "").strip()
    s = s.replace("|", "∣")
    return " ".join(s.split())


def _steps_cell(text: Any) -> str:
    """Numbered steps, one per line in the cell: "1. Open 2. Paste" is how the model
    writes them and how nobody reads them. `<br>` is the painter's line break."""
    s = _cell(text)
    parts = [x for x in _STEP_RE.split(s) if x is not None and x.strip()]
    # re.split with one group yields [lead?, num, rest, num, rest, ...]
    nums = [i for i, x in enumerate(parts) if x.isdigit()]
    if len(nums) < 2:
        return s
    out: list[str] = []
    if nums[0] > 0:
        out.append(" ".join(parts[:nums[0]]))
    for j, i in enumerate(nums):
        end = nums[j + 1] if j + 1 < len(nums) else len(parts)
        body = " ".join(parts[i + 1:end]).strip()
        out.append(f"{parts[i]}. {body}" if body else f"{parts[i]}.")
    return "<br>".join(out)


def _scenario_key(scenario: str) -> tuple[int, str]:
    s = (scenario or "").strip()
    for i, known in enumerate(SCENARIO_ORDER):
        if s.lower() == known.lower():
            return i, known
    return len(SCENARIO_ORDER), s or "Other"


def test_cases_markdown(test_cases: Iterable[Any], results: Optional[list[dict]] = None) -> str:
    """Markdown for the document body. `test_cases` are TestCase models or dicts."""
    rows: list[dict] = []
    for tc in test_cases:
        d = tc.model_dump() if hasattr(tc, "model_dump") else dict(tc)
        rows.append(d)

    groups: dict[str, list[dict]] = {}
    for d in rows:
        _, label = _scenario_key(d.get("scenario_type") or "")
        groups.setdefault(label, []).append(d)
    ordered = sorted(groups.items(), key=lambda kv: _scenario_key(kv[0]))

    parts: list[str] = []
    for label, cases in ordered:
        parts.append(f"## {SECTION_TITLE.get(label, label.upper() + ' CASES')}\n")
        parts.append(f"{len(cases)} test case{'s' if len(cases) != 1 else ''}.\n")
        parts.append("| ID | Tests | Summary | Test data | Steps | Expected result |")
        parts.append("|----|-------|---------|-----------|-------|-----------------|")
        for d in cases:
            parts.append(
                f"| {_cell(d.get('test_case_id'))} | {_cell(d.get('feature_or_function_tested'))} | "
                f"{_cell(d.get('test_summary'))} | {_cell(d.get('test_data'))} | "
                f"{_steps_cell(d.get('test_steps'))} | {_cell(d.get('expected_result'))} |"
            )
        parts.append("")

    if results:
        parts.append("## EXECUTION RESULTS\n")
        passed = sum(1 for r in results if str(r.get("status", "")).lower() in ("pass", "passed"))
        parts.append(f"{passed} of {len(results)} passed.\n")
        parts.append("| Test | Result | Detail |")
        parts.append("|------|--------|--------|")
        for r in results:
            name = r.get("name") or r.get("id") or r.get("test_case_id") or ""
            parts.append(f"| {_cell(name)} | {_cell(r.get('status'))} | {_cell(r.get('detail') or r.get('message') or '')} |")
        parts.append("")

    if not parts:
        parts.append("## TEST CASES\n\nNo test cases were generated.\n")
    return "\n".join(parts).rstrip() + "\n"


def render_test_cases_docx(test_cases: Iterable[Any], path: str, *, meta: TestDocMeta,
                           results: Optional[list[dict]] = None) -> str:
    """Write the designed test case document at `path`. Returns the path."""
    cases = list(test_cases)
    markdown = test_cases_markdown(cases, results)
    kinds = ", ".join(meta.test_types) if meta.test_types else ""
    return render_markdown_docx(
        markdown, path,
        title=meta.title, subject="Test case document",
        eyebrow="TEST CASE DOCUMENT", eyebrow_tail=kinds,
        subtitle=f"{len(cases)} test case{'s' if len(cases) != 1 else ''}"
                 + (f" · {len(results)} executed" if results else ""),
        meta_line=meta.status,
        facts=[
            ("Project", meta.project),
            ("Derived from", meta.source.split(":", 1)[-1].strip() if ":" in meta.source else meta.source),
            ("Test types", kinds),
            ("Generated", meta.generated_on),
        ],
        footer=f"{meta.title} · Test case document",
    )
