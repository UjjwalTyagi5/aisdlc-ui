"""The Project Manager agent's documents — filed under ITS stage, as Word, with a page view.

WHAT IT DID. This agent borrowed the Design agent's `export_document`, which registers
every file under the `design` stage. An effort estimate exported from the Project Manager
page therefore landed in Design's Documents: the Plan page showed "0 artifacts", nothing
could be raised from it, and Requests & Approvals never heard of it. The model had also
chosen `.md`, so the chat's link opened raw markdown in the browser.

The tool itself is shared — see shared/tools/stage_documents — and bound here to `plan`.
"""
from __future__ import annotations

from shared.tools.stage_documents import make_export_document_tool, page_markdown  # noqa: F401

export_document = make_export_document_tool(
    stage="plan", agent_name="Project Manager", eyebrow="PROJECT PLAN",
    default_filename="project_plan.docx", examples=" (an effort estimate, a plan, a status report)",
)
