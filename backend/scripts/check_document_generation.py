"""Generate one of every document this platform produces, and read each back.

Run it on a new host — a Linux VM especially — before trusting a deployment. Each of these
formats is a different library with different system requirements, and they fail in different
ways: python-docx and python-pptx are pure Python and always work; WeasyPrint links the Pango
and GLib C libraries and raises at IMPORT time when they are missing; fonts are a separate
question again, because a PDF renders boxes instead of text on a host with none installed.

    cd backend
    uv run python scripts/check_document_generation.py

Writes into a temporary directory that is removed afterwards. Needs no database, no network
and no configuration. Exit code 0 when every format was produced and read back.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MARKDOWN = """# Deployment check

## 01 Summary
A **designed** document with a table, a list and a heading.

| Layer | Technology | Status |
|---|---|---|
| Frontend | Next.js | Passed |
| Backend | FastAPI | Passed |

- first item
- second item

## 02 Notes
Nothing here needs a database.
"""

_results: list[tuple[bool, str, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    _results.append((ok, name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:34} {detail}")


def check_word(out: Path) -> None:
    """The designed Word document — `shared/docs` (python-docx), what every agent files."""
    from docx import Document

    from shared.docs.markdown_docx import render_markdown_docx

    path = out / "check.docx"
    render_markdown_docx(
        MARKDOWN, str(path), title="Deployment check", eyebrow="DEPLOYMENT CHECK",
        subtitle="Generated to prove the Word path works on this host.",
        facts=[("Host", "linux"), ("Format", "docx")], footer="deployment check",
    )
    doc = Document(str(path))
    paragraphs = sum(1 for p in doc.paragraphs if p.text.strip())
    record("Word (.docx)", path.stat().st_size > 5000 and paragraphs > 0,
           f"{path.stat().st_size:,} bytes, {paragraphs} paragraphs, {len(doc.tables)} tables")


def check_pptx(out: Path) -> None:
    """A deck, as the Design and Requirements agents build one (python-pptx)."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    path = out / "check.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Deployment check"
    body = slide.placeholders[1].text_frame
    body.text = "Slide generation works on this host"
    body.add_paragraph().text = "python-pptx needs nothing from the system"
    table_slide = prs.slides.add_slide(prs.slide_layouts[5])
    table_slide.shapes.title.text = "A table"
    grid = table_slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(1)).table
    grid.cell(0, 0).text, grid.cell(0, 1).text = "Layer", "Status"
    grid.cell(1, 0).text, grid.cell(1, 1).text = "Deck", "Passed"
    table_slide.notes_slide.notes_text_frame.text = "Speaker notes survive the round trip."
    prs.slides[0].shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(36)
    prs.save(str(path))

    read = Presentation(str(path))
    titles = [s.shapes.title.text for s in read.slides if s.shapes.title]
    record("PowerPoint (.pptx)", len(read.slides.__iter__.__self__._sldIdLst) >= 2 and "Deployment check" in titles,
           f"{path.stat().st_size:,} bytes, {len(titles)} slides with titles")


def check_xlsx(out: Path) -> None:
    """A workbook — the Project Manager's estimates, the Testing agent's suites (openpyxl)."""
    from openpyxl import load_workbook

    from shared.tools.xlsx_render import markdown_to_xlsx

    path = out / "check.xlsx"
    markdown_to_xlsx(MARKDOWN, str(path), sheet_title="Deployment check")
    wb = load_workbook(str(path))
    sheet = wb[wb.sheetnames[0]]
    rows = [[c.value for c in row] for row in sheet.iter_rows()]
    record("Excel (.xlsx)", path.stat().st_size > 2000 and len(rows) > 1,
           f"{path.stat().st_size:,} bytes, sheets={wb.sheetnames}, {len(rows)} rows")


def check_pdf(out: Path) -> None:
    """The PDF the app falls back to without Word — reportlab, pure Python but font-dependent."""
    from shared.tools.pdf_render import markdown_to_pdf

    path = out / "check.pdf"
    markdown_to_pdf(MARKDOWN, str(path), title="Deployment check")
    head = path.read_bytes()[:5]
    record("PDF (reportlab fallback)", head == b"%PDF-" and path.stat().st_size > 1000,
           f"{path.stat().st_size:,} bytes, header {head!r}")


def check_weasyprint(out: Path) -> None:
    """The QA report's PDF. The one renderer that links C libraries — see docs/deploy-linux-vm.md."""
    path = out / "check-weasyprint.pdf"
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001 — the missing-libraries case this check exists for
        record("PDF (WeasyPrint, QA report)", False,
               f"import failed: {type(exc).__name__}: {str(exc)[:90]}")
        return
    HTML(string="<h1>Deployment check</h1><p>WeasyPrint rendered this.</p>").write_pdf(str(path))
    head = path.read_bytes()[:5]
    record("PDF (WeasyPrint, QA report)", head == b"%PDF-" and path.stat().st_size > 1000,
           f"{path.stat().st_size:,} bytes, header {head!r}")


def check_docx_to_pdf() -> None:
    """Word-driven conversion: Windows/macOS only. On Linux the app uses the fallback above."""
    if sys.platform == "win32":
        try:
            import docx2pdf  # noqa: F401
            record("Word-to-PDF via Word", True, "docx2pdf importable (Word required at run time)")
        except Exception as exc:  # noqa: BLE001
            record("Word-to-PDF via Word", False, f"{type(exc).__name__}")
        return
    print("  [note] Word-to-PDF via Word: not available off Windows — the app falls back to "
          "the reportlab PDF above (docs/deploy-linux-vm.md §8).")


def main() -> int:
    print(f"document generation check — python {sys.version.split()[0]} on {sys.platform}\n")
    with tempfile.TemporaryDirectory(prefix="doc-check-") as tmp:
        out = Path(tmp)
        for check in (check_word, check_pptx, check_xlsx, check_pdf, check_weasyprint):
            try:
                check(out)
            except Exception as exc:  # noqa: BLE001 — a failed format is the result, not a crash
                record(check.__name__.replace("check_", ""), False, f"{type(exc).__name__}: {str(exc)[:90]}")
        check_docx_to_pdf()

    failed = [r for r in _results if not r[0]]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} formats generated and read back")
    for _, name, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
