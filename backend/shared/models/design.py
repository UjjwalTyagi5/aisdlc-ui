from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel


class DesignArtifacts(BaseModel):
    hld: Optional[str] = None
    lld: Optional[str] = None
    c4_diagrams: Optional[str] = None
    api_contract: Optional[str] = None
    database_schema: Optional[str] = None
    adrs: Optional[str] = None
    tech_stack: Optional[str] = None
    security_checklist: Optional[str] = None
    full_document: Optional[str] = None
    docx_url: Optional[str] = None
    linked_work_item_ids: Optional[List[int]] = None
    linked_ado_ids: Optional[List[int]] = None  # deprecated — use linked_work_item_ids


# Section headers exactly as used in the DESIGN_SYS_MESSAGE prompt.
# Matching is case-insensitive so minor prompt variations don't break parsing.
_SECTION_MAP: dict[str, str] = {
    "hld": "HIGH-LEVEL DESIGN",
    "lld": "LOW-LEVEL DESIGN",
    "c4_diagrams": "C4 ARCHITECTURE DIAGRAMS",
    "api_contract": "API CONTRACT",
    "database_schema": "DATABASE SCHEMA",
    "adrs": "ARCHITECTURE DECISION RECORDS",
    "tech_stack": "TECHNOLOGY STACK",
    "security_checklist": "SECURITY DESIGN CHECKLIST",
}


def _extract_section(content: str, header: str) -> Optional[str]:
    """Return the text under `## HEADER` up to the next `## ` heading or end-of-string."""
    pattern = rf"##\s+{re.escape(header)}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def parse_artifact_sections(content: str, docx_url: Optional[str] = None) -> DesignArtifacts:
    """Parse the 7 mandatory design sections from a full markdown document.

    Args:
        content: Raw markdown produced by the Design Agent.
        docx_url: Optional URL of a generated .docx file to attach.

    Returns a DesignArtifacts instance. Sections not found in content are None.
    """
    return DesignArtifacts(
        **{field: _extract_section(content, header) for field, header in _SECTION_MAP.items()},
        full_document=content,
        docx_url=docx_url,
    )
