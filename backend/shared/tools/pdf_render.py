"""Render Markdown to PDF.

WHY REPORTLAB. The obvious choice, weasyprint, is already a dependency and is used by
the testing agent's QA report — but it fails to import on this platform ("could not
import some external libraries": it needs GTK/Pango native DLLs that are not present on
Windows). A document tool that raises on import is worse than no tool, so the renderer
that works everywhere Python does is the one used here.

DELIBERATELY A SMALL SUBSET of Markdown: headings, paragraphs, bullets, numbered lists,
tables, code blocks and horizontal rules. A design or requirements document is prose,
headings and the occasional table; supporting inline HTML or nested structures would
add a rendering engine's worth of edge cases for output nobody reads closely.
"""
from __future__ import annotations

import html
import logging
import os
import re
from typing import List

logger = logging.getLogger(__name__)

_HEADING_SIZES = {1: 18, 2: 15, 3: 13, 4: 11.5, 5: 11, 6: 10.5}


def _inline(text: str) -> str:
    """Markdown emphasis to ReportLab's mini-HTML, with everything else escaped.

    Escaping FIRST and then re-introducing only the tags we generate is what stops a
    stray '<' or '&' in a requirement ("latency < 300ms", "R&D") from raising a parse
    error deep inside the PDF build and losing the whole document.
    """
    out = html.escape(text)
    out = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", out)
    out = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', out)
    # [text](url) -> a real link, because a PDF of a design doc is often read detached
    # from the chat that produced it.
    out = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)", r'<link href="\2"><u>\1</u></link>', out)
    return out


def _table(rows: List[List[str]], styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    body = [[Paragraph(_inline(c), styles["cell"]) for c in r] for r in rows]
    t = Table(body, repeatRows=1, hAlign="LEFT", colWidths=[None] * len(rows[0]))
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B7C3")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    t.spaceAfter = 4 * mm
    return t


def markdown_to_pdf(content: str, output_path: str, title: str = "") -> str:
    """Write `content` (Markdown) to `output_path` as a PDF. Returns the path.

    Raises on a genuine failure so the calling tool can report it — a PDF that silently
    is not written is worse than an error, because the agent would tell the user their
    document is ready.
    """
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=10, leading=14,
                               spaceAfter=5, alignment=TA_LEFT),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontSize=8.5, leading=11),
        "code": ParagraphStyle("code", parent=base["Code"], fontSize=8.5, leading=11,
                               backColor="#F4F5F7", borderPadding=4, spaceAfter=6),
    }
    for lvl, size in _HEADING_SIZES.items():
        styles[f"h{lvl}"] = ParagraphStyle(
            f"h{lvl}", parent=base["Heading1"], fontSize=size,
            leading=size * 1.3, spaceBefore=10 if lvl > 1 else 0, spaceAfter=5,
        )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=title or os.path.basename(output_path),
    )

    flow: list = []
    if title:
        flow += [Paragraph(_inline(title), styles["h1"]), Spacer(1, 4 * mm)]

    lines = (content or "").replace("\r\n", "\n").split("\n")
    i, bullets, numbered, table, code = 0, [], [], [], None

    def flush_lists():
        nonlocal bullets, numbered
        for items, kind in ((bullets, "bullet"), (numbered, "1")):
            if items:
                flow.append(
                    ListFlowable(
                        [ListItem(Paragraph(_inline(x), styles["body"])) for x in items],
                        bulletType=kind, leftIndent=14,
                    )
                )
                flow.append(Spacer(1, 2 * mm))
        bullets, numbered = [], []

    def flush_table():
        nonlocal table
        if table:
            flow.append(_table(table, styles))
        table = []

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        if line.strip().startswith("```"):
            if code is None:
                code = []
            else:
                flow.append(Paragraph(html.escape("\n".join(code)).replace("\n", "<br/>"),
                                      styles["code"]))
                code = None
            i += 1
            continue
        if code is not None:
            code.append(raw)
            i += 1
            continue

        # A table is a run of pipe rows; the |---|---| separator is layout, not data.
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells if c):
                table.append(cells)
            i += 1
            continue
        flush_table()

        heading = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if heading:
            flush_lists()
            lvl = len(heading.group(1))
            flow.append(Paragraph(_inline(heading.group(2)), styles[f"h{lvl}"]))
            i += 1
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            numbered and flush_lists()
            bullets.append(re.sub(r"^\s*[-*+]\s+", "", line))
            i += 1
            continue
        if re.match(r"^\s*\d+[.)]\s+", line):
            bullets and flush_lists()
            numbered.append(re.sub(r"^\s*\d+[.)]\s+", "", line))
            i += 1
            continue
        flush_lists()

        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            flow.append(HRFlowable(width="100%", thickness=0.6, spaceBefore=4, spaceAfter=6))
            i += 1
            continue

        if line.strip():
            flow.append(Paragraph(_inline(line.strip()), styles["body"]))
        i += 1

    flush_lists()
    flush_table()
    if code:  # an unterminated fence still renders rather than vanishing
        flow.append(Paragraph(html.escape("\n".join(code)).replace("\n", "<br/>"),
                              styles["code"]))
    if not flow:
        flow.append(Paragraph("(empty document)", styles["body"]))

    doc.build(flow)
    return output_path
