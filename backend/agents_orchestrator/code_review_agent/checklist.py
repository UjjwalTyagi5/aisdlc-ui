"""The code review checklist — structured, filed as Word, and shown on the page's Checklist tab.

WHY A TOOL OF ITS OWN. Asked for a checklist, the agent wrote free markdown — once into
Confluence, once into chat with "paste this into Word". The Checklist tab needs the checks
as data it can lay out (section, check, how to verify, status), so the checklist is written
in ONE fixed shape: this module renders it to a Word file whose page copy is that same
shape, and the page parses the page copy back (frontend `parseChecklist`). The two must
change together.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from langchain_core.tools import tool

STATUSES = {"pass": "Pass", "fail": "Fail", "n/a": "N/A", "na": "N/A", "not_applicable": "N/A",
            "to_check": "To check", "todo": "To check", "": "To check"}
HEADER = "| # | Check | How to verify | Status | Note |"


def _cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("|", "/")).strip() or "—"


def normalise(sections: Any) -> list[dict]:
    """[{section, items: [{check, how, status, note}]}], with empty checks dropped. Raises
    ValueError when nothing usable is left."""
    if isinstance(sections, str):
        sections = json.loads(sections)
    if isinstance(sections, dict):
        sections = sections.get("sections") or []
    out = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        items = []
        for it in sec.get("items") or []:
            if isinstance(it, str):
                it = {"check": it}
            if not isinstance(it, dict) or not str(it.get("check") or "").strip():
                continue
            status = STATUSES.get(str(it.get("status") or "").strip().lower().replace(" ", "_"), "To check")
            items.append({"check": str(it["check"]).strip(), "how": str(it.get("how") or it.get("how_to_verify") or "").strip(),
                          "status": status, "note": str(it.get("note") or "").strip()})
        if items:
            out.append({"section": str(sec.get("section") or sec.get("title") or "Checks").strip(), "items": items})
    if not out:
        raise ValueError("the checklist has no checks")
    return out


def checklist_markdown(title: str, intro: str, sections: list[dict]) -> str:
    """The page copy and the Word body. One table per section, always HEADER's columns."""
    blocks = []
    if intro.strip():
        blocks.append(intro.strip())
    counts = {"Pass": 0, "Fail": 0, "N/A": 0, "To check": 0}
    for sec in sections:
        for it in sec["items"]:
            counts[it["status"]] = counts.get(it["status"], 0) + 1
    total = sum(counts.values())
    blocks.append(f"{total} checks · {counts['Pass']} pass · {counts['Fail']} fail · "
                  f"{counts['N/A']} not applicable · {counts['To check']} to check")
    n = 0
    for sec in sections:
        rows = [HEADER, "|---|---|---|---|---|"]
        for it in sec["items"]:
            n += 1
            rows.append(f"| {n} | {_cell(it['check'])} | {_cell(it['how'])} | {it['status']} | {_cell(it['note'])} |")
        blocks.append(f"## {sec['section']}\n\n" + "\n".join(rows))
    return "\n\n".join(blocks)


@tool
async def create_review_checklist(title: str, sections_json: str, intro: str = "", filename: str = "") -> str:
    """Create a CODE REVIEW CHECKLIST: shown on the page's Checklist tab AND filed as a Word
    document (a DRAFT the user can raise for approval). Use this — not export_document — whenever
    the user asks for a checklist.

    When a review is open in this conversation, fill `status` for each check from that review's
    actual results (pass / fail / n/a) and say why in `note`; otherwise leave status "to_check".
    Never mark a check pass that the review did not establish.

    Args:
        title: e.g. "QuickLink Code Review Checklist".
        sections_json: JSON list of sections:
            [{"section": "Security", "items": [{"check": "No hardcoded secrets",
              "how": "Gitleaks passed on the branch", "status": "pass", "note": "0 secrets"}]}]
            Cover at least: Security, Requirements, Architecture and design, Logic and correctness,
            Performance, Maintainability, Testing, Documentation.
        intro: optional one-paragraph purpose of the checklist.
        filename: optional, e.g. "QuickLink_Code_Review_Checklist.docx".
    """
    from shared.tools.stage_documents import _output_path, file_document, write_word  # noqa: PLC0415

    try:
        sections = normalise(sections_json)
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Error: sections_json is not a usable checklist ({exc}). Send a JSON list of sections with items."
    title = (title or "Code Review Checklist").strip()
    stem = re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(os.path.basename(filename or ""))[0] or title).strip("_")
    if "checklist" not in stem.lower():
        stem += "_Checklist"
    path, name, user_id, session_id = _output_path(f"{stem}.docx")
    markdown = checklist_markdown(title, intro, sections)
    items = [it for sec in sections for it in sec["items"]]
    try:
        write_word(markdown, path, title=title, eyebrow="CODE REVIEW CHECKLIST", agent_name="Code Review", facts=[
            ("Checks", str(len(items))),
            ("Pass", str(sum(1 for i in items if i["status"] == "Pass"))),
            ("Fail", str(sum(1 for i in items if i["status"] == "Fail"))),
            ("To check", str(sum(1 for i in items if i["status"] == "To check"))),
            ("Sections", str(len(sections))),
        ])
    except Exception as exc:  # noqa: BLE001 — said, not swallowed
        return f"Error: the checklist document could not be written ({type(exc).__name__}: {str(exc)[:200]}). Nothing was filed."
    filed = await file_document(path, name, user_id, session_id, stage="code_review",
                                agent_name="Code Review", kind="checklist")
    if filed.get("error"):
        return filed["error"]
    return (f"Checklist created: '{name}' ({len(items)} checks in {len(sections)} sections) — {filed['url']}\n"
            "It is on the page's Checklist tab and filed in the Code Review documents as a DRAFT. Give the "
            "user this link exactly as written. If they want it approved, call raise_document_for_approval "
            f"with \"{name}\".")
