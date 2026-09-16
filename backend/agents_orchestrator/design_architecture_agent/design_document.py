"""The Design agent's Word document, painted with the platform's document identity.

WHY NOT THE GENERIC CONVERTER. `shared/tools/docx_tools.markdown_to_docx` turns
markdown into Word's defaults: "Heading 1", "Table Grid", Courier for code, no title
page, no identity. Beside the Track 3 migration-intent brief — a designed document with
a title band, a key-facts strip and one PwC palette — it read as a different product's
paperwork. This renderer walks the same markdown and paints it with the brief's
primitives (`shared/docs/pwc_style.WordCanvas`), so the two documents are one family.

WHAT IT DRAWS, block by block:

  · a title band — "DESIGN DOCUMENT · <components>", the title, the project, the
    date and status — and a key-facts strip (project, source, components, generated);
  · each `## HEADER` the markdown holds as a numbered section over an orange rule,
    labelled with the catalogue's name for it, and only those — the agent produces
    what was asked for, and the document shows exactly that;
  · `###` / `####` as subheads; paragraphs with bold, italic, inline code and links;
  · bullets and numbered lists, nested by indent;
  · markdown tables with the white→orange header row; the first column in ink;
  · fenced code (DDL, YAML, JSON) in a grey monospace panel labelled with its language;
  · Mermaid blocks as images with a numbered caption, falling back to the code —
    labelled as unrendered — when the renderer returns nothing;
  · `> ⚠️ [ASSUMPTION]` blockquotes as a callout with an amber edge (needs confirming);
    other blockquotes as a grey card.

ONE COLOUR, ONE MEANING, as the palette module states it. Brand orange is a fill or a
rule here, never text; assumption is amber; a High/Critical risk is a red pill; green
appears once, for an ADR that is Accepted ("fine as is"). An `#### ADR-nnn` heading
opens a card with an orange edge that holds the decision's fields.

SYNCHRONOUS. It writes a file and calls `render_mermaid` (network) per diagram;
callers on the event loop run it in an executor, as `architecture._markdown_to_docx`
does. `fetch_image` is likewise a plain callable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from agents_orchestrator.design_architecture_agent import components as dc
from shared.docs import pwc_style as st

RenderMermaid = Callable[[str], Optional[bytes]]
FetchImage = Callable[[str], Optional[bytes]]


@dataclass
class DesignMeta:
    """What the title band and facts strip say. Everything optional but the title."""

    title: str
    project: str = ""
    components: list[str] = field(default_factory=list)
    source: str = ""
    generated_on: str = ""
    status: str = "Awaiting approval"
    track: str = ""


# The painter itself lives in `shared/docs/markdown_docx` — the Testing agent's test
# case document paints on the same canvas. Re-exported here because the Design
# agent's tests, and the section labels below, address it through this module.
from shared.docs.markdown_docx import (  # noqa: E402,F401
    ASSUMPTION_TAG_RUN,
    inline_runs,
    paint_markdown,
    pill_for,
    render_markdown_docx,
)


def _section_label(header: str) -> str:
    cid = dc.component_for_header(header)
    if cid:
        return dc.BY_ID[cid].label
    return header.strip()


def scope_label(components: list[str]) -> str:
    """"Full design document" or "2 of 8 components" — the overview is not a pick."""
    pickable = [c.id for c in dc.COMPONENTS if c.id != "overview"]
    chosen = [c for c in components if c in pickable]
    if not chosen:
        return ""
    if len(chosen) == len(pickable):
        return "Full design document"
    return f"{len(chosen)} of {len(pickable)} components"


def render_design_docx(markdown: str, path: str, *, meta: DesignMeta,
                       render_mermaid: Optional[RenderMermaid] = None,
                       fetch_image: Optional[FetchImage] = None) -> str:
    """Write the design document at `path`. Returns the path.

    Each fact appears once. The band says what the document IS (kind, track, title,
    the components it holds, its status); the strip says where it CAME FROM and how
    much of the full design it is — "2 of 8 components" tells a reviewer at a glance
    that this is a scoped document, not an unfinished one.
    """
    return render_markdown_docx(
        markdown, path,
        title=meta.title, subject="Design document",
        eyebrow="DESIGN DOCUMENT", eyebrow_tail=meta.track,
        subtitle=dc.labels_for(meta.components) if meta.components else "",
        meta_line=meta.status,
        facts=[
            ("Project", meta.project),
            ("Source", meta.source),
            ("Scope", scope_label(meta.components)),
            ("Generated", meta.generated_on),
        ],
        footer=f"{meta.title} · Design document",
        section_label=_section_label,
        render_mermaid=render_mermaid, fetch_image=fetch_image,
    )
