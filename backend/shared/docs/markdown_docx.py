"""Markdown, painted with the platform's document identity.

WHY NOT THE GENERIC CONVERTER. `shared/tools/docx_tools.markdown_to_docx` turns
markdown into Word's defaults: "Heading 1", "Table Grid", Courier for code, no title
page, no identity. Beside the Track 3 migration-intent brief — a designed document with
a title band, a key-facts strip and one PwC palette — it read as a different product's
paperwork. This walks the same markdown and paints it with the brief's primitives
(`shared/docs/pwc_style.WordCanvas`), so every agent's document is one family. The
Design agent's document was the first (design_document.py); the Testing agent's test
case document is the second, and it is why the painter lives here rather than in
either agent.

WHAT IT DRAWS, block by block:

  · each `## HEADER` as a numbered section over an orange rule, labelled by the
    caller's `section_label` hook (the Design agent maps catalogue headers to
    component names; a document with no such mapping keeps the header);
  · `###` / `####` as subheads; paragraphs with bold, italic, inline code and links;
  · bullets and numbered lists, nested by indent;
  · markdown tables with the white→orange header row; the first column in ink;
  · fenced code (DDL, YAML, JSON) in a grey monospace panel labelled with its language;
  · Mermaid blocks as images with a numbered caption, falling back to the code —
    labelled as unrendered — when the renderer returns nothing;
  · `> ⚠️ [ASSUMPTION]` blockquotes as a callout with an amber edge (needs confirming);
    other blockquotes as a grey card;
  · `#### ADR-nnn` headings as a card with an orange edge holding the decision's fields.

ONE COLOUR, ONE MEANING, as the palette module states it. Brand orange is a fill or a
rule here, never text; assumption is amber; a High/Critical risk is a red pill; green
appears once, for a status that is Accepted / Pass ("fine as is").

SYNCHRONOUS. It writes a file and calls `render_mermaid` (network) per diagram;
callers on the event loop run it in an executor. `fetch_image` is likewise a plain
callable.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from shared.docs import pwc_style as st

RenderMermaid = Callable[[str], Optional[bytes]]
FetchImage = Callable[[str], Optional[bytes]]


# ── inline markdown ───────────────────────────────────────────────────────────

# Underscore emphasis is recognised only at word edges: `store_id, price_cents` is a
# column list, not "store<i>id, price</i>cents". The [ASSUMPTION] marker is the
# agent's own convention (see components._GROUNDING) and paints as a tag, not brackets.
_INLINE_RE = re.compile(
    r"(\*\*(?P<bold>.+?)\*\*)"
    r"|(`(?P<code>[^`]+)`)"
    r"|(\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)]+)\))"
    r"|(\*(?P<italic>[^*\n]+)\*)"
    r"|((?<![A-Za-z0-9_])_(?P<italic2>[^\n]+?)_(?![A-Za-z0-9_]))"
    r"|(?P<tag>\[ASSUMPTION\])",
    re.IGNORECASE,
)

ASSUMPTION_TAG_RUN = {"bold": True, "size": 7.5, "colour": st.ACCENT_DEEP, "tint": st.AMBER_TINT, "spacing": 10}


def inline_runs(text: str) -> list[tuple[str, dict]]:
    """`[(text, run kwargs)]` for a line of markdown — bold, italic, code and links."""
    out: list[tuple[str, dict]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], {}))
        if m.group("bold") is not None:
            out.append((m.group("bold"), {"bold": True, "colour": st.INK}))
        elif m.group("code") is not None:
            out.append((m.group("code"), {"font": st.CODE_FONT, "size": 9, "colour": st.ACCENT_DEEP, "tint": st.TINT_5}))
        elif m.group("ltext") is not None:
            out.append((m.group("ltext"), {"bold": True, "colour": st.ACCENT}))
            out.append((f" ({m.group('lurl')})", {"size": 8.5, "colour": st.MUTED}))
        elif m.group("italic") is not None:
            out.append((m.group("italic"), {"italic": True}))
        elif m.group("italic2") is not None:
            out.append((m.group("italic2"), {"italic": True}))
        elif m.group("tag") is not None:
            out.append((" ASSUMPTION ", dict(ASSUMPTION_TAG_RUN)))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], {}))
    return [(t, dict(kw)) for t, kw in out if t]


def _paint_runs(canvas: st.WordCanvas, p, runs: list[tuple[str, dict]], *, size: float = 10,
                colour: str = st.BODY) -> None:
    for text, kw in runs:
        kw = dict(kw)
        run_size, run_colour = kw.pop("size", size), kw.pop("colour", colour)
        # `<br>` is a line break inside a cell or paragraph — numbered steps in a
        # table cell read one per line; markdown itself has no way to say so.
        for i, piece in enumerate(text.split("<br>")):
            if i:
                p.add_run().add_break()
            if piece:
                canvas.run(p, piece, size=run_size, colour=run_colour, **kw)


# ── judgement words ───────────────────────────────────────────────────────────
#
# The palette's rule (from the brief): red = risk, amber = needs confirming, green
# only "fine as is", grey = context. A table cell that IS one of these judgements
# paints as a pill so the eye finds it; everything else stays body text.

_ADR_HEADING_RE = re.compile(r"^ADR[-\s]?\d+", re.IGNORECASE)
_SEVERITY_COLUMN_RE = re.compile(r"risk|severity|priority|impact|likelihood", re.IGNORECASE)

_PILLS: dict[str, tuple[str, str]] = {
    # judgement → (tint, ink)
    "critical": (st.RED_TINT, st.RED),
    "high": (st.RED_TINT, st.RED),
    "medium": (st.AMBER_TINT, st.ACCENT_DEEP),
    "low": (st.GREY_TINT, st.GREY_INK),
    "accepted": (st.GREEN_TINT, st.GREEN),
    "proposed": (st.AMBER_TINT, st.ACCENT_DEEP),
    "deprecated": (st.GREY_TINT, st.GREY_INK),
    "superseded": (st.GREY_TINT, st.GREY_INK),
    "rejected": (st.GREY_TINT, st.GREY_INK),
    # a test result: green only for "fine as is"
    "pass": (st.GREEN_TINT, st.GREEN),
    "passed": (st.GREEN_TINT, st.GREEN),
    "fail": (st.RED_TINT, st.RED),
    "failed": (st.RED_TINT, st.RED),
    "blocked": (st.AMBER_TINT, st.ACCENT_DEEP),
    "skipped": (st.GREY_TINT, st.GREY_INK),
}

#: Judgements that name a state rather than a degree — a pill in any column.
_STATE_WORDS = frozenset({
    "accepted", "proposed", "deprecated", "superseded", "rejected",
    "pass", "passed", "fail", "failed", "blocked", "skipped",
})


def pill_for(value: str, *, column: str = "") -> Optional[tuple[str, str]]:
    """(tint, ink) when `value` is a judgement word in a column that carries one —
    a severity column, or an ADR's Status — else None. "High" in a column called
    "Talks to" is a name, not a verdict, so the column is required."""
    key = (value or "").strip().strip("*").lower()
    if key not in _PILLS:
        return None
    if _SEVERITY_COLUMN_RE.search(column or "") or key in _STATE_WORDS:
        return _PILLS[key]
    return None


# ── block markdown ────────────────────────────────────────────────────────────

# "⚠️ **[ASSUMPTION]** — " and its plainer forms, at the start of a callout.
_ASSUMPTION_MARK_RE = re.compile(
    r"^\s*(?:\u26a0\ufe0f?\s*)?\*{0,2}\[ASSUMPTION\]\*{0,2}\s*[—:\-]?\s*",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BULLET_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")


def _split_row(line: str) -> list[str]:
    cells = line.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|"):
        cells = cells[:-1]
    return [c.strip() for c in cells.split("|")]


#: How a `## HEADER` is labelled on the page. The Design agent maps its catalogue
#: headers to component names; a document with no such mapping keeps the header.
SectionLabel = Callable[[str], str]


def _keep_header(header: str) -> str:
    return header.strip()


class _Painter:
    def __init__(self, canvas: st.WordCanvas, render_mermaid: Optional[RenderMermaid],
                 fetch_image: Optional[FetchImage], section_label: SectionLabel = _keep_header):
        self.c = canvas
        self.render_mermaid = render_mermaid
        self.fetch_image = fetch_image
        self.section_label = section_label
        self.current_section = ""
        # An ADR is a card: `#### ADR-001: Title` opens one with an orange edge and
        # every block until the next heading paints inside it — its Field/Details
        # table as label-over-value rows, so a decision reads as one unit.
        self.card_cell = None

    # ── blocks ──────────────────────────────────────────────────────────────
    def heading(self, level: int, text: str) -> None:
        text = text.strip()
        self.close_card()
        if level == 1:
            return  # the title lives in the band
        if level == 2:
            self.current_section = self.section_label(text)
            self.c.heading(self.current_section)
        elif level == 3:
            self.c.subhead(text, size=10.5, before=10)
        elif _ADR_HEADING_RE.match(text):
            self.open_adr_card(text)
            return
        else:
            self.c.subhead(text, size=9.5, colour=st.ACCENT_DEEP, before=8)

    def open_adr_card(self, title: str) -> None:
        cell = self.c.card(tint=st.TINT_5, edge=(st.ACCENT, 24), pad=(120, 120, 220, 180))
        ident, _, rest = title.partition(":")
        p = self.c.para(cell, first=True, after=3)
        self.c.run(p, ident.strip().upper(), size=7.5, bold=True, colour=st.ACCENT_DEEP, spacing=20)
        if rest.strip():
            p = self.c.para(cell, after=4, keep=True)
            _paint_runs(self.c, p, inline_runs(rest.strip()), size=10.5, colour=st.INK)
            for r in p.runs:
                r.bold = True
        self.card_cell = cell

    def close_card(self) -> None:
        if self.card_cell is not None:
            self.card_cell = None
            self.c.para(after=4)

    @property
    def _container(self):
        return self.card_cell if self.card_cell is not None else self.c.doc

    def paragraph(self, lines: list[str]) -> None:
        text = " ".join(line.strip() for line in lines).strip()
        if not text:
            return
        m = _IMAGE_RE.match(text)
        if m:
            self.image(m.group(1), m.group(2))
            return
        p = self.c.para(self._container, after=4)
        _paint_runs(self.c, p, inline_runs(text))

    def image(self, alt: str, url: str) -> None:
        self.close_card()
        png = None
        if self.fetch_image:
            try:
                png = self.fetch_image(url)
            except Exception:  # noqa: BLE001 — a missing image must not cost the document
                png = None
        if png:
            try:
                self.c.figure(png, alt or self.current_section)
                return
            except Exception:  # noqa: BLE001
                pass
        p = self.c.para(after=4)
        self.c.run(p, f"[Image could not be loaded: {alt or url}]", size=9, italic=True, colour=st.MUTED)

    def bullets(self, items: list[tuple[int, str, bool]]) -> None:
        """items: (indent level, text, numbered)."""
        n = 0
        for level, text, numbered in items:
            if numbered:
                n += 1
                mark = f"{n}."
            else:
                mark = "•"
            self.c.bullet(self._container, inline_runs(text), mark=mark, level=level)
        self.c.para(self._container, after=2)

    def table(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        cols = max(len(r) for r in rows)
        rows = [r + [""] * (cols - len(r)) for r in rows]
        header, body = rows[0], rows[1:]
        if self.card_cell is not None and cols == 2:
            self.card_fields(body)
            return
        self.close_card()
        # Column widths: the first column a little wider when it labels the row —
        # unless it is a short key (ID, #, Ref), which wants less room, not more.
        widths = [st.PAGE_WIDTH_MM / cols] * cols
        if cols >= 3:
            key_like = (header[0] or "").strip().strip("*").lower() in ("id", "#", "no", "no.", "ref", "key", "tc")
            widths[0] = st.PAGE_WIDTH_MM * (0.13 if key_like else 0.26)
            rest = (st.PAGE_WIDTH_MM - widths[0]) / (cols - 1)
            widths[1:] = [rest] * (cols - 1)
        t = self.c.table(len(body) + 1, widths, rules="rows")
        self.c.head_row(t.rows[0], header)
        for r, row in enumerate(body, start=1):
            for i, value in enumerate(row):
                p = self.c.para(t.rows[r].cells[i], first=True, after=0)
                pill = pill_for(value, column=header[i] if i < len(header) else "")
                if pill:
                    self.c.pill(p, value.strip().strip("*"), pill[0], pill[1])
                    continue
                runs = inline_runs(value or "—")
                if i == 0:
                    runs = [(txt, {**kw, "bold": True, "colour": st.INK}) for txt, kw in runs]
                _paint_runs(self.c, p, runs, size=9)
        self.c.para(after=2)

    def card_fields(self, rows: list[list[str]]) -> None:
        """An ADR's Field/Details table, inside its card: label over value."""
        cell = self.card_cell
        for label, value in rows:
            p = self.c.para(cell, before=4, after=0, keep=True)
            self.c.run(p, label.strip().strip("*"), size=7.5, bold=True, colour=st.MUTED, caps=True, spacing=10)
            p = self.c.para(cell, after=2)
            pill = pill_for(value, column=label)
            if pill:
                self.c.pill(p, value.strip().strip("*"), pill[0], pill[1])
            else:
                _paint_runs(self.c, p, inline_runs(value or "—"), size=9.5, colour=st.INK)

    def code(self, language: str, body: str) -> None:
        self.close_card()
        if language.lower() == "mermaid":
            self.mermaid(body)
            return
        self.c.code_panel(body, language=language)

    def mermaid(self, code: str) -> None:
        png = None
        if self.render_mermaid:
            try:
                png = self.render_mermaid(code)
            except Exception:  # noqa: BLE001 — a diagram must not cost the document
                png = None
        if png:
            try:
                self.c.figure(png, self.current_section)
                return
            except Exception:  # noqa: BLE001 — bad bytes fall through to the code
                pass
        self.c.code_panel(code, language="mermaid — diagram could not be rendered")

    def quote(self, lines: list[str]) -> None:
        text = " ".join(line.strip() for line in lines).strip()
        if not text:
            return
        self.close_card()
        is_assumption = "[ASSUMPTION]" in text.upper()
        if is_assumption:
            cell = self.c.card(tint=st.AMBER_TINT, edge=(st.AMBER, 24), pad=(110, 110, 200, 160))
            p = self.c.para(cell, first=True, after=2)
            self.c.run(p, "ASSUMPTION — NEEDS CONFIRMING", size=7.5, bold=True, colour=st.ACCENT_DEEP, spacing=20)
            body = _ASSUMPTION_MARK_RE.sub("", text, count=1).strip()
            p = self.c.para(cell, after=0)
            _paint_runs(self.c, p, inline_runs(body or text), size=9.5, colour=st.INK)
        else:
            cell = self.c.card(tint=st.PANEL, edge=(st.FAINT, 18), pad=(110, 110, 200, 160))
            p = self.c.para(cell, first=True, after=0)
            _paint_runs(self.c, p, inline_runs(text), size=9.5, colour=st.BODY)
        self.c.para(after=2)


def paint_markdown(canvas: st.WordCanvas, markdown: str, *, render_mermaid: Optional[RenderMermaid] = None,
                   fetch_image: Optional[FetchImage] = None,
                   section_label: SectionLabel = _keep_header) -> None:
    """Walk `markdown` block by block and paint it onto `canvas`."""
    painter = _Painter(canvas, render_mermaid, fetch_image, section_label)
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    para: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            painter.paragraph(para)
            para = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        fence = _FENCE_RE.match(stripped)
        if fence:
            flush_para()
            language = fence.group(1) or ""
            body: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence
            painter.code(language, "\n".join(body))
            continue

        if stripped.startswith("#"):
            flush_para()
            level = len(stripped) - len(stripped.lstrip("#"))
            painter.heading(level, stripped[level:].strip().rstrip("#").strip())
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            flush_para()
            rows = [_split_row(line)]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            painter.table(rows)
            continue

        if stripped.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            painter.quote(quote)
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_para()
            items: list[tuple[int, str, bool]] = []
            base_indent: Optional[int] = None
            while i < n:
                m = _BULLET_RE.match(lines[i])
                if not m:
                    # a wrapped continuation line belongs to the previous item
                    if items and lines[i].strip() and lines[i].startswith((" ", "\t")):
                        lvl, txt, num = items[-1]
                        items[-1] = (lvl, f"{txt} {lines[i].strip()}", num)
                        i += 1
                        continue
                    break
                indent = len(m.group(1).replace("\t", "    "))
                if base_indent is None:
                    base_indent = indent
                level = max(0, (indent - base_indent) // 2)
                items.append((min(level, 3), m.group(3).strip(), m.group(2)[0].isdigit()))
                i += 1
            painter.bullets(items)
            continue

        if stripped in ("---", "***", "___"):
            flush_para()
            i += 1
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        para.append(line)
        i += 1
    flush_para()
    painter.close_card()


def render_markdown_docx(
    markdown: str, path: str, *, title: str, eyebrow: str, eyebrow_tail: str = "",
    subtitle: str = "", meta_line: str = "", facts: list[tuple[str, str]] | None = None,
    footer: str = "", subject: str = "", section_label: SectionLabel = _keep_header,
    render_mermaid: Optional[RenderMermaid] = None, fetch_image: Optional[FetchImage] = None,
) -> str:
    """Write a designed document at `path`: the title band, the key-facts strip
    (blank facts are dropped), then `markdown` painted section by section. Returns
    the path."""
    canvas = st.WordCanvas(title=title, subject=subject or eyebrow.title())
    canvas.title_band(
        eyebrow=eyebrow, eyebrow_tail=eyebrow_tail, title=title,
        subtitle=subtitle, meta_line=meta_line,
    )
    canvas.facts(list(facts or []))
    paint_markdown(canvas, markdown, render_mermaid=render_mermaid, fetch_image=fetch_image,
                   section_label=section_label)
    canvas.footer(footer or title)
    canvas.save(path)
    return path
