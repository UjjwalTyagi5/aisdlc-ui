"""The palette and the Word primitives every designed document on this platform uses.

WHY THIS IS SHARED. The Track 3 migration-intent brief was the first document built as
a designed artefact — a white→orange title band, a key-facts strip, cards with a
coloured edge, header rows stepping into orange — and the direction from the user's
boss was that documents must read at a glance and look enterprise-grade. The Design
agent's document is the second. Two documents with two palettes and two sets of
primitives would be two products; this module is what makes them one.

ONE COLOUR, ONE MEANING (the brief's rule, kept):
  grey    today, and anything that is context
  orange  the plan: the design, the recommendation, what changes (PwC orange)
  red     end of life and risks
  amber   needs confirming: an assumption, a legacy dot — never a fill
  green   fine as it is — never text

CONTRAST. Orange TEXT is `ACCENT` (5.2:1 on white, 4.6:1 on TINT_10) or `ACCENT_DEEP`
on a tint (6.1:1). `BRAND` itself is 3.3:1 on white: a fill, a rule or a mark, never
text. Body text is `BODY` (10.4:1); `MUTED` (5.3:1) is the floor for small labels.

`WordCanvas` holds the primitives. It is deliberately ignorant of any document's
content: it draws bands, strips, tables, panels and runs; the document modules decide
what goes in them.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any, Optional

# ── the palette ───────────────────────────────────────────────────────────────

INK = "1F1F1F"
BODY = "3D3D3D"
MUTED = "6B6B6B"
FAINT = "A3A3A3"  # rules, ticks and arrows only: too light for text
RULE = "E5E5E5"
PANEL = "F5F5F5"
WHITE = "FFFFFF"
BRAND = "FD5108"
ACCENT = "C2410C"
ACCENT_DEEP = "9A3412"
TINT_5, TINT_8, TINT_10, TINT_12, TINT_15, TINT_20 = "FFF6F3", "FFF1EB", "FFEEE6", "FFEAE1", "FFE5DA", "FFDCCE"
TINT_30, TINT_45, TINT_60, TINT_80 = "FECBB5", "FEB190", "FE976B", "FD7439"
RED, RED_TINT = "B42318", "FDECEC"
AMBER, AMBER_TINT = "E09A00", "FFF7E0"
GREEN, GREEN_TINT = "16A34A", "E3F4EA"
GREY_TINT, GREY_INK = "EDEDED", "525252"

#: A Word header row, white into orange, one tint step per column.
HEAD_STEPS = (TINT_5, TINT_8, TINT_10, TINT_12, TINT_15)

TEXT_FONT = "Calibri"
CODE_FONT = "Consolas"

#: A4 with the brief's margins: 174 mm of usable width.
PAGE_WIDTH_MM = 174.0


def rgb(hex_: str) -> tuple[int, int, int]:
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def blend(a: str, b: str, f: float) -> str:
    """The colour `f` of the way from `a` to `b`."""
    ra, rb = rgb(a), rgb(b)
    return "".join(f"{round(x + (y - x) * f):02X}" for x, y in zip(ra, rb))


# ── the canvas ────────────────────────────────────────────────────────────────


class WordCanvas:
    """A python-docx document with the platform's page setup and drawing primitives.

    Every method that takes a `container` accepts the document or a table cell, so a
    paragraph, a bullet list or a subhead can be painted inside a card as easily as on
    the page.
    """

    _TCPR_AFTER_SHD = ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
                       "w:hideMark", "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge", "w:tcPrChange")

    def __init__(self, *, title: str, subject: str) -> None:
        from docx import Document  # noqa: PLC0415
        from docx.shared import Mm, Pt, RGBColor  # noqa: PLC0415

        self.Pt, self.Mm, self.RGB = Pt, Mm, RGBColor
        self.doc = Document()
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Mm(210), Mm(297)
        sec.left_margin = sec.right_margin = Mm(18)
        sec.top_margin, sec.bottom_margin = Mm(15), Mm(16)
        normal = self.doc.styles["Normal"]
        normal.font.name = TEXT_FONT
        normal.font.size = Pt(10)
        normal.font.color.rgb = RGBColor.from_string(BODY)
        self._east_asia(normal.element)
        normal.paragraph_format.space_after = Pt(2)
        normal.paragraph_format.line_spacing = 1.08
        self.doc.core_properties.title = title
        self.doc.core_properties.subject = subject
        self.section_count = 0
        self.figure_count = 0

    # ── XML helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def el(tag: str, **attrs):
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415

        el = OxmlElement(tag)
        for k, v in attrs.items():
            el.set(qn(f"w:{k}"), str(v))
        return el

    def _east_asia(self, style_el) -> None:
        from docx.oxml.ns import qn  # noqa: PLC0415

        rpr = style_el.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = self.el("w:rFonts")
            rpr.insert(0, fonts)
        for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn(f"w:{attr}"), TEXT_FONT)

    def shade(self, cell, fill: str) -> None:
        from docx.oxml.ns import qn  # noqa: PLC0415

        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:shd")):
            tcPr.remove(old)
        tcPr.insert_element_before(self.el("w:shd", val="clear", color="auto", fill=fill),
                                   *self._TCPR_AFTER_SHD)

    def pad(self, cell, top=70, bottom=70, left=110, right=110) -> None:
        from docx.oxml.ns import qn  # noqa: PLC0415

        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:tcMar")):
            tcPr.remove(old)
        mar = self.el("w:tcMar")
        for side, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
            mar.append(self.el(f"w:{side}", w=v, type="dxa"))
        tcPr.insert_element_before(mar, *self._TCPR_AFTER_SHD[2:])

    def cell_border(self, cell, **edges) -> None:
        """edges: left=("D2541F", 24) — a coloured bar on one side of a card."""
        from docx.oxml.ns import qn  # noqa: PLC0415

        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:tcBorders")):
            tcPr.remove(old)
        borders = self.el("w:tcBorders")
        for side in ("top", "left", "bottom", "right"):
            if side in edges:
                colour, size = edges[side]
                borders.append(self.el(f"w:{side}", val="single", sz=size, space=0, color=colour))
        tcPr.insert_element_before(borders, "w:shd", *self._TCPR_AFTER_SHD)

    def valign(self, cell, where: str = "center") -> None:
        from docx.oxml.ns import qn  # noqa: PLC0415

        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:vAlign")):
            tcPr.remove(old)
        tcPr.append(self.el("w:vAlign", val=where))

    def table(self, rows: int, widths_mm: list[float], *, rules: str = "none",
              rule_colour: str = RULE, gutter: int = 0):
        """A fixed-layout table. rules: none | rows (horizontal lines) | grid."""
        from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415

        t = self.doc.add_table(rows=rows, cols=len(widths_mm))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        tblPr = t._tbl.tblPr
        borders = self.el("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            on = (rules == "grid") or (rules == "rows" and edge in ("insideH", "bottom"))
            if gutter and edge in ("insideV", "insideH"):
                borders.append(self.el(f"w:{edge}", val="single", sz=gutter, space=0, color=WHITE))
            elif on:
                borders.append(self.el(f"w:{edge}", val="single", sz=4, space=0, color=rule_colour))
            else:
                borders.append(self.el(f"w:{edge}", val="nil"))
        tblPr.insert_element_before(borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
                                    "w:tblCaption", "w:tblDescription", "w:tblPrChange")
        tblPr.insert_element_before(self.el("w:tblLayout", type="fixed"), "w:tblCellMar", "w:tblLook",
                                    "w:tblCaption", "w:tblDescription", "w:tblPrChange")
        for old in tblPr.findall(qn("w:tblW")):
            old.set(qn("w:type"), "dxa")
            old.set(qn("w:w"), str(int(sum(widths_mm) * 56.7)))
        for row in t.rows:
            for i, w in enumerate(widths_mm):
                row.cells[i].width = self.Mm(w)
                self.pad(row.cells[i])
        return t

    # ── text helpers ─────────────────────────────────────────────────────────
    def run(self, p, text: str, *, size: float = 10, bold=False, italic=False, colour: str = BODY,
            tint: Optional[str] = None, caps=False, spacing: Optional[int] = None,
            font: Optional[str] = None):
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        r.font.size = self.Pt(size)
        r.font.color.rgb = self.RGB.from_string(colour)
        if font:
            r.font.name = font
            rfonts = r._r.get_or_add_rPr().find(self.qn("w:rFonts"))
            if rfonts is not None:
                for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                    rfonts.set(self.qn(f"w:{attr}"), font)
        if caps:
            r.font.all_caps = True
        rpr = r._r.get_or_add_rPr()
        after_shd = ("w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang",
                     "w:eastAsianLayout", "w:specVanish", "w:oMath")
        if spacing is not None:  # schema order: spacing comes before sz, shd
            rpr.insert_element_before(self.el("w:spacing", val=spacing), "w:w", "w:kern", "w:position",
                                      "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect", "w:bdr",
                                      "w:shd", *after_shd)
        if tint:
            rpr.insert_element_before(self.el("w:shd", val="clear", color="auto", fill=tint), *after_shd)
        return r

    @staticmethod
    def qn(tag: str):
        from docx.oxml.ns import qn  # noqa: PLC0415

        return qn(tag)

    def para(self, container=None, *, before: float = 0, after: float = 2, align=None, keep=False,
             first=False, indent_mm: float = 0):
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415

        container = container if container is not None else self.doc
        if first and getattr(container, "paragraphs", None):
            p = container.paragraphs[0]
        else:
            p = container.add_paragraph()
        pf = p.paragraph_format
        pf.space_before, pf.space_after = self.Pt(before), self.Pt(after)
        if indent_mm:
            pf.left_indent = self.Mm(indent_mm)
        if align == "center":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif align == "right":
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        if keep:
            pf.keep_with_next = True
        return p

    def pill(self, p, text: str, tint: str, ink: str, size: float = 8.5) -> None:
        self.run(p, f" {text} ", size=size, bold=True, colour=ink, tint=tint)

    def heading(self, title: str, *, number: Optional[int] = None) -> None:
        """A numbered section heading over a grey rule. Numbers itself when none given."""
        if number is None:
            self.section_count += 1
            number = self.section_count
        p = self.doc.add_paragraph()
        pPr = p._p.get_or_add_pPr()
        bdr = self.el("w:pBdr")
        bdr.append(self.el("w:bottom", val="single", sz=6, space=4, color=RULE))
        pPr.append(bdr)
        pf = p.paragraph_format
        pf.space_before, pf.space_after, pf.keep_with_next = self.Pt(16), self.Pt(7), True
        self.run(p, f"{number:02d}  ", size=12.5, bold=True, colour=ACCENT)
        self.run(p, title, size=12.5, bold=True, colour=INK)

    def subhead(self, text: str, container=None, colour: str = INK, before: float = 6,
                size: float = 9.5) -> None:
        p = self.para(container, before=before, after=2, keep=True)
        self.run(p, text, size=size, bold=True, colour=colour)

    def eyebrow(self, text: str, container=None, colour: str = MUTED, before: float = 8) -> None:
        p = self.para(container, before=before, after=2, keep=True)
        self.run(p, text, size=7.5, bold=True, colour=colour, caps=True, spacing=20)

    def bullet(self, container, text_runs: list[tuple[str, dict]], *, mark: str = "•",
               mark_colour: str = ACCENT, size: float = 9.5, level: int = 0, first: bool = False):
        """One bullet whose text is a list of (text, run kwargs) so inline bold/code survive."""
        p = self.para(container, after=1.5, first=first)
        pf = p.paragraph_format
        pf.left_indent, pf.first_line_indent = self.Mm(4 + 5 * level), self.Mm(-4)
        self.run(p, f"{mark}  ", size=size, bold=True, colour=mark_colour)
        for text, kw in text_runs:
            kw = dict(kw)  # a run may carry its own size (inline code) and colour
            self.run(p, text, size=kw.pop("size", size), colour=kw.pop("colour", BODY), **kw)
        return p

    # ── the designed blocks ──────────────────────────────────────────────────
    def title_band(self, *, eyebrow: str, eyebrow_tail: str, title: str, subtitle: str,
                   meta_line: str) -> None:
        """A strip running into PwC orange over a warm band. Word has no gradient fill
        for a table cell, so the strip is 24 cells stepping through the ramp."""
        from docx.enum.table import WD_ROW_HEIGHT_RULE  # noqa: PLC0415

        steps = 24
        t = self.table(2, [PAGE_WIDTH_MM / steps] * steps)
        strip = t.rows[0]
        strip.height, strip.height_rule = self.Mm(1.8), WD_ROW_HEIGHT_RULE.EXACTLY
        for i, cell in enumerate(strip.cells):
            self.shade(cell, blend(TINT_15, BRAND, i / (steps - 1)))
            self.pad(cell, top=0, bottom=0, left=0, right=0)
        cell = t.rows[1].cells[0].merge(t.rows[1].cells[-1])
        self.shade(cell, TINT_8)
        self.pad(cell, top=300, bottom=300, left=340, right=340)
        p = self.para(cell, after=4, first=True)
        self.run(p, eyebrow, size=8, bold=True, colour=ACCENT_DEEP, spacing=30)
        if eyebrow_tail:
            self.run(p, f"   ·   {eyebrow_tail}", size=8, colour=MUTED, spacing=20)
        p = self.para(cell, after=4)
        self.run(p, title, size=24, bold=True, colour=INK)
        if subtitle:
            p = self.para(cell, after=6)
            self.run(p, subtitle, size=11, colour=BODY)
        p = self.para(cell, after=0)
        self.run(p, meta_line or "Draft", size=8.5, colour=MUTED)

    def facts(self, pairs: list[tuple[str, str]]) -> None:
        """The key-facts strip: label over value, grey panels with a white gutter."""
        pairs = [(label, value) for label, value in pairs if value]
        if not pairs:
            return
        self.para(after=2)
        width = PAGE_WIDTH_MM / len(pairs)
        t = self.table(1, [width] * len(pairs), gutter=36)
        for i, (label, value) in enumerate(pairs):
            cell = t.rows[0].cells[i]
            self.shade(cell, PANEL)
            self.pad(cell, top=110, bottom=110, left=150, right=110)
            p = self.para(cell, after=1, first=True)
            self.run(p, label, size=7.5, bold=True, colour=MUTED, caps=True, spacing=10)
            p = self.para(cell, after=0)
            self.run(p, value, size=11.5 if len(value) < 24 else 9.5, bold=True, colour=INK)

    def head_row(self, row, labels: list[str]) -> None:
        """A header row running white into orange, one tint step per column."""
        last = len(HEAD_STEPS) - 1
        for i, head in enumerate(labels):
            cell = row.cells[i]
            self.shade(cell, HEAD_STEPS[round(i * last / max(len(labels) - 1, 1))])
            p = self.para(cell, first=True, after=0)
            self.run(p, head, size=7.5, bold=True, colour=ACCENT_DEEP, spacing=10)

    def card(self, *, tint: str, edge: Optional[tuple[str, int]] = None, pad=(140, 140, 220, 180)):
        """A full-width single cell, shaded, optionally with a coloured left edge.
        Returns the cell; paint into it with `para`, `subhead`, `bullet`."""
        t = self.table(1, [PAGE_WIDTH_MM])
        cell = t.rows[0].cells[0]
        self.shade(cell, tint)
        if edge:
            self.cell_border(cell, left=edge)
        self.pad(cell, top=pad[0], bottom=pad[1], left=pad[2], right=pad[3])
        return cell

    def code_panel(self, code: str, *, language: str = "") -> None:
        """Code in a grey panel, in the code face, one paragraph per line."""
        cell = self.card(tint=PANEL, pad=(110, 110, 160, 160))
        if language:
            p = self.para(cell, after=3, first=True)
            self.run(p, language.upper(), size=7, bold=True, colour=MUTED, spacing=20, font=CODE_FONT)
            first = False
        else:
            first = True
        lines = code.rstrip("\n").splitlines() or [""]
        for i, line in enumerate(lines):
            p = self.para(cell, after=0, first=first and i == 0)
            p.paragraph_format.line_spacing = 1.0
            self.run(p, line.replace("\t", "    ") or " ", size=8.5, colour=INK, font=CODE_FONT)
        self.para(after=2)

    def figure(self, png: bytes, caption: str, *, width_mm: float = 150) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415

        self.figure_count += 1
        self.doc.add_picture(BytesIO(png), width=self.Mm(width_mm))
        self.doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        p = self.para(after=8, align="center")
        self.run(p, f"Figure {self.figure_count}", size=8, bold=True, colour=ACCENT)
        if caption:
            self.run(p, f"  ·  {caption}", size=8, colour=MUTED)

    def footer(self, text: str) -> None:
        """`text · Page N` in the page footer."""
        from docx.oxml.ns import qn  # noqa: PLC0415

        sec = self.doc.sections[0]
        p = sec.footer.paragraphs[0]
        self.run(p, f"{text} · Page ", size=8, colour=MUTED)
        r = p.add_run()
        r.font.size = self.Pt(8)
        r.font.color.rgb = self.RGB.from_string(MUTED)
        begin = self.el("w:fldChar", fldCharType="begin")
        instr = self.el("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = "PAGE"
        end = self.el("w:fldChar", fldCharType="end")
        r._r.append(begin)
        r._r.append(instr)
        r._r.append(end)

    def save(self, path: str) -> None:
        import os  # noqa: PLC0415

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.doc.save(path)
