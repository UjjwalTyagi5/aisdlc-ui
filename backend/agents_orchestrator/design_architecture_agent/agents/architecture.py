"""Design & Architecture Agent — LangGraph workflow (Azure OpenAI).

Tools:
- read_uploaded_file  — extract text from an uploaded local file (by path)
- read_document       — read an APPROVED project document (by id), from
                       shared/tools/project_documents.make_document_tools
- generate_architecture — produce HLD/LLD/C4/API/DB/ADR from text or context
- generate_architecture_from_context — architecture from conversation context only
- update_response     — refine/update previously generated content
- save_architecture   — persist content to a .docx file
- markdowntodoc       — convert any markdown to .docx
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import re
import traceback
from typing import Annotated, Any, List, Optional, TypedDict

import aiofiles
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from config.checkpoint import build_checkpointer as _build_checkpointer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from shared.tools.mcp_runtime import get_mcp_tools, MCP_TOOLS_PROMPT_NOTE
from shared.services.skill_runtime import get_skill_tools

from config import sdlcSettings
from config.connection_manager import manager
import logging

from config.env import (
    AGENTIC_BASE_URL,
    ANTHROPIC_MODEL,
)

logger = logging.getLogger(__name__)
from config.ws_helper import broadcast_log, get_session_id, get_user_id, get_provider_kind
from config.connectors.context import get_connector
from config.connectors.base import ConnectorNotAvailableError
from agents_orchestrator.design_architecture_agent.config import shared
from agents_orchestrator.design_architecture_agent import components as _components
from shared.tools.spectral_tool import run_spectral_lint
from agents_orchestrator.design_architecture_agent.tools.schema_validation_tool import validate_database_schema
from agents_orchestrator.design_architecture_agent.tools.existing_system_tool import analyze_existing_system
from agents_orchestrator.design_architecture_agent.tools.figma_tools import (
    export_figma_frames,
    list_figma_frames,
    read_figma_design,
)

load_dotenv()

esett = sdlcSettings()

# Derive files directory from this file's location so it always matches
# the static server mount in process_api.py regardless of AGENTIC_APP_PATH in .env
# architecture.py → agents/ → design_architecture_agent/ → agents_orchestrator/ → agentic_app/
_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[3] / "files")


async def _llm_generate_async(prompt: str, system: str = "") -> str:
    """Stream generation tokens directly to WebSocket as they arrive.

    Each token is broadcast immediately so the user sees content building in
    real time instead of waiting for the full response. Extended thinking is
    intentionally disabled — it adds 100-150 s of latency for no quality gain
    on well-specified architecture tasks.
    """
    from shared.services.model_resolver import get_resolved_model
    resolved = get_resolved_model()
    if resolved is None:
        raise RuntimeError(
            "No model resolved for this run — an administrator must configure a model provider.")

    session_id = get_session_id()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    full_text = []
    try:
        # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
        import litellm  # noqa: F401 — kept for other uses in this module
        from shared.observability.litellm_trace import (  # noqa: PLC0415
            traced_acompletion,
        )
        from shared.services.model_resolver import temperature_kwargs  # noqa: PLC0415

        response = await traced_acompletion(
            name="design:architecture",
            model=resolved.model,
            custom_llm_provider=resolved.litellm_provider,
            api_key=resolved.api_key,
            api_base=resolved.base_url,
            messages=messages,
            max_tokens=8192,
            # Omitted entirely for gpt-5-family models — see temperature_kwargs.
            **temperature_kwargs(resolved.model, 0.25),
            stream=True,
            timeout=120,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_text.append(delta)
                if session_id:
                    # broadcast_to_session, NOT broadcast. `broadcast` falls back to
                    # EVERY active connection when the session id has no registered
                    # socket, and an Orchestrator run has none: orchestrator2's socket
                    # layer never touches this ConnectionManager. So a design document
                    # generated through the Orchestrator was streamed, token by token,
                    # onto whatever unrelated legacy agent socket happened to be open.
                    # This method exists for payloads that must never fan out and never
                    # falls back. The Design page is unaffected — it registers the
                    # session on every frame before the graph runs.
                    await manager.broadcast_to_session({
                        "type": "stream_chunk",
                        "content": delta,
                        "session_id": session_id,
                    })
    except Exception as exc:
        # If we already streamed a substantial document, a trailing/network error on the
        # closing chunk must NOT discard the whole generation (that surfaces as
        # "Tool ... failed: <entire doc>" and loses the .docx). Return what we have so it
        # can still be saved/exported; only propagate when nothing usable was produced.
        streamed = "".join(full_text)
        if len(streamed.strip()) > 200:
            logger.warning("_llm_generate_async: returning partial stream after error: %s", exc)
            broadcast_log(manager, "Generation finished with a trailing warning (content preserved).", level="INFO")
            return streamed
        broadcast_log(manager, f"Generation error: {exc}", level="ERROR")
        raise
    return "".join(full_text)


# ── File text extraction — delegated to shared utility ─────────────────────────
from shared.tools.document_tools import extract_file_text as _extract_text_from_path


# ── Markdown → DOCX helper ─────────────────────────────────────────────────────

def _add_hyperlink(paragraph, text, url):
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rStyle = OxmlElement("w:rStyle")
    rStyle.set(qn("w:val"), "Hyperlink")
    rPr.append(rStyle)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _render_mermaid_to_png(code: str) -> bytes | None:
    """Render Mermaid diagram code to PNG bytes.

    Tries mermaid.ink first; falls back to kroki.io on any failure.
    Returns None if both services fail so the caller can degrade gracefully.
    """
    code = code.strip()
    # Primary: mermaid.ink
    try:
        encoded = base64.urlsafe_b64encode(code.encode()).decode()
        resp = requests.get(f"https://mermaid.ink/img/{encoded}?type=png", timeout=15)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    # Fallback: kroki.io
    try:
        resp = requests.post(
            "https://kroki.io/mermaid/png",
            data=code.encode(),
            headers={"Content-Type": "text/plain"},
            timeout=20,
        )
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    return None


def _fetch_image_bytes_sync(url: str) -> bytes | None:
    """Fetch PNG/image bytes from a URL synchronously (run in executor)."""
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    return None


async def _fetch_image_bytes(url: str) -> bytes | None:
    """Async wrapper — fetches image from URL in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_image_bytes_sync, url)


async def _design_meta(markdown: str) -> "DesignMeta":
    """What the title band and facts strip say about this document.

    The title is the document's own `#` heading (the same one `_architecture_filename`
    slugs); the components are whichever sections the document holds; the project's
    display name and track come from the run's project when there is one. Every
    lookup degrades to blank — a document with an empty facts cell is still the
    document; one that failed to save over a project-name lookup is not.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from agents_orchestrator.design_architecture_agent.design_document import DesignMeta  # noqa: PLC0415

    match = re.search(r"(?m)^\s{0,3}#\s+(.+?)\s*$", markdown or "")
    title = (match.group(1).strip() if match else "") or "Design document"
    project_name, track = "", ""
    try:
        from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

        project_id, tenant_id = get_project_id(), get_tenant_id()
        if project_id and tenant_id:
            import uuid as _uuid  # noqa: PLC0415

            from shared.db import get_db_session_for_tenant  # noqa: PLC0415
            from shared.models.orm import Project  # noqa: PLC0415

            async with get_db_session_for_tenant(tenant_id) as session:
                project = await session.get(Project, _uuid.UUID(str(project_id)))
                if project is not None:
                    project_name = getattr(project, "display_name", "") or ""
                    track_id = getattr(project, "track", "") or ""
                    track = {
                        "greenfield": "Track 1 · Greenfield",
                        "enhancement": "Track 2 · Enhancement",
                        "modernization": "Track 3 · Code Modernization",
                    }.get(track_id, "")
    except Exception:  # noqa: BLE001 — a blank fact, never a lost document
        logger.info("design document: project lookup skipped", exc_info=True)
    return DesignMeta(
        title=title,
        project=project_name,
        components=_components.sections_present(markdown),
        source=getattr(shared, "design_source", "") or "",
        generated_on=datetime.now(timezone.utc).strftime("%d %b %Y"),
        track=track,
    )


async def _markdown_to_docx(markdown_string: str, docx_path: str) -> str:
    """Write the DESIGNED document — the platform's title band, facts strip, palette
    and primitives (`design_document.render_design_docx`) — not Word's defaults.

    Rendering is synchronous (it writes a file and fetches each diagram), so it runs
    in an executor to keep the socket's event loop free, exactly as the generic
    converter ran the mermaid renderer.
    """
    from agents_orchestrator.design_architecture_agent.design_document import (  # noqa: PLC0415
        render_design_docx,
    )

    meta = await _design_meta(markdown_string)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: render_design_docx(
            markdown_string, docx_path, meta=meta,
            render_mermaid=_render_mermaid_to_png,
            fetch_image=_fetch_image_bytes_sync,
        ),
    )
    # THE MARKDOWN GOES BESIDE THE FILE. `register_generated_file` keeps this sibling with
    # the document as its page copy, which is what the app renders when the document is
    # opened (shared/services/artifact_page.py). Without it a design could only be
    # downloaded: clicking it showed a file card. Best-effort — a document that cannot
    # write its copy is still a document.
    try:
        with open(os.path.splitext(docx_path)[0] + ".md", "w", encoding="utf-8") as fh:
            fh.write(markdown_string)
    except OSError:
        logger.warning("design document: page copy not written beside %s", docx_path, exc_info=True)

    result = f"Successfully saved document to '{docx_path}'"
    # Update shared state so the API layer can broadcast file_generated
    try:
        session_id = get_session_id()
        user_id = get_user_id()
        file_url = f"{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{os.path.basename(docx_path)}"
        shared.output_file = os.path.basename(docx_path)
        shared.output_file_url = file_url
    except Exception:
        pass
    return result


def _docx_to_pdf(docx_path: str, pdf_path: str) -> None:
    """Convert the designed .docx to PDF with Word, through `docx2pdf`.

    Synchronous and COM-bound: it runs in an executor thread, and COM on a worker
    thread needs its own apartment (`CoInitialize`) or Word's Dispatch fails with
    "CoInitialize has not been called". Raises whatever the conversion raises — the
    caller decides what to do without Word.
    """
    import sys  # noqa: PLC0415

    from docx2pdf import convert  # noqa: PLC0415

    initialised = False
    if sys.platform == "win32":
        try:
            import pythoncom  # noqa: PLC0415

            pythoncom.CoInitialize()
            initialised = True
        except Exception:  # noqa: BLE001 — pywin32 missing: let convert() report it
            pass
    try:
        convert(docx_path, pdf_path)
    finally:
        if initialised:
            pythoncom.CoUninitialize()
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise RuntimeError("Word produced no PDF")


_PDF_CONVERSION_TIMEOUT_S = 120.0


async def _markdown_to_pdf(markdown_string: str, pdf_path: str, *, title: str = "") -> None:
    """The PDF is the DESIGNED Word document, converted — Word first, PDF from it.

    Rendering the markdown a second time with a plain PDF renderer would give the
    user two documents that do not look alike. So: render the .docx to a scratch
    name beside the PDF, convert it with Word, remove the scratch file, and only
    when Word is not there (a Linux host, no COM, a hung conversion) fall back to the
    plain renderer so the user still gets a PDF. The scratch .docx is removed because
    nothing announces it: a file in the output directory that no reply links to is
    one the user cannot reach, and `save_architecture` makes a Word file on request.

    BOTH scratch files go. `_markdown_to_docx` also writes the document's markdown
    beside itself as the page copy the app renders (`artifact_page.sibling_markdown_path`),
    so converting to PDF left a `<name>.pdf-source.md` in the output directory — a file
    no reply links to, named after a scratch file, sitting next to every PDF the Design
    agent produced. A PDF needs no page copy: the browser draws the PDF itself.
    """
    docx_path = os.path.splitext(pdf_path)[0] + ".pdf-source.docx"
    scratch = (docx_path, os.path.splitext(docx_path)[0] + ".md")
    try:
        await _markdown_to_docx(markdown_string, docx_path)
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _docx_to_pdf(docx_path, pdf_path)),
            timeout=_PDF_CONVERSION_TIMEOUT_S,
        )
        return
    except Exception:  # noqa: BLE001
        logger.warning(
            "design document: Word conversion unavailable, rendering the PDF plainly",
            exc_info=True,
        )
    finally:
        for path in scratch:
            try:
                os.remove(path)
            except OSError:
                pass

    from shared.tools.pdf_render import markdown_to_pdf  # noqa: PLC0415

    await asyncio.get_event_loop().run_in_executor(
        None, lambda: markdown_to_pdf(markdown_string, pdf_path, title=title),
    )


# ── Saving the document the agent just generated ───────────────────────────────
#
# These four helpers exist because of ONE LIVE INCIDENT, twice over. Run through the
# Orchestrator, the Design agent's entire reply was 104 characters:
#
#     "The architecture document has been generated. Would you like me to save it as
#      a .docx file for download?"
#
# and nothing reached disk. The Project Manager had written
# `Coffee_Ordering_App_Delivery_Plan.pdf` into `files/<user>/orchestrator/<run>/output/`
# on the same run; the Design agent's Deliverables heading was empty. The prompt had
# told the model to OFFER a save, and an offer is a question — an Orchestrator turn
# ends when the agent stops talking, so nobody ever answered it.
#
# Generating a document and writing it down are one act, so the generating tools now do
# both. `save_architecture` remains for a DIFFERENT name or format, which is the only
# thing the user still has to ask for.
#
# They deliberately live next to `_markdown_to_docx`: that is the function that turns a
# document into a file, and this is the policy about when it runs. `_design_output_dir`
# and `_design_broadcast_file` are defined further down the module and resolved at call
# time.


def _architecture_filename(markdown: str) -> str:
    """A filename derived from the document's own title.

    Design, the Project Manager and Testing all write into the SAME
    `orchestrator/<run>/output/` directory (see tests/orchestrator2/
    test_stage_output_dirs.py for the mapping), and a run can design more than once.
    A fixed `architecture.docx` therefore silently overwrites the previous design and
    tells a reader of the file tree nothing — which is why the Project Manager's export
    is `Coffee_Ordering_App_Delivery_Plan.pdf` and not `plan.pdf`.

    Falls back to `architecture.docx` when the document has no level-1 heading, or when
    its heading survives slugging as nothing usable. Two designs with the SAME title
    still collide; that is the pre-existing behaviour of every save tool here and is not
    something this function claims to solve.
    """
    match = re.search(r"(?m)^\s{0,3}#\s+(.+?)\s*$", markdown or "")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", match.group(1) if match else "").strip("_")
    # 80 characters, not the filesystem's limit: the run directory and the user id are
    # already in the path, and a name longer than this is not read, only scrolled.
    return f"{slug[:80]}.docx" if slug else "architecture.docx"


async def _write_architecture_docx(content: str, filename: str) -> tuple[str, str]:
    """Write ONE architecture .docx into this run's output directory and announce it.

    Returns `(path, download_url)`. An empty url means the announcement failed, NOT
    that nothing was written — `_design_broadcast_file` swallows its own errors and
    returns "" so that a dead websocket cannot cost the user the file.
    """
    full_path = os.path.join(_design_output_dir(), filename)
    await _markdown_to_docx(content, full_path)
    url = await _design_broadcast_file(filename, full_path)
    return full_path, url


async def _autosave_architecture(markdown: str) -> str:
    """Save a freshly generated architecture document, unasked, and return a receipt.

    Returns a one-line "SAVED: …" receipt the model can quote, or "" when nothing was
    written — which happens in two cases, both of which must leave the caller holding
    the document:

    · NO SESSION CONTEXT. Outside a run there is no user and no session id, and the
      path would be `files/None/orchestrator/None/output/` behind a `/generated/None/…`
      URL that resolves for nobody. An unasked-for save declines rather than littering;
      an explicit `save_architecture` is still free to write wherever it is told.

    · THE WRITE FAILED. The document is minutes of work and real tokens against the
      tenant's own provider. Trading it for an OSError would be the more expensive bug,
      so the failure is logged at exception level and the receipt comes back empty —
      the CALLER then returns the document unchanged, so the user can still be shown it
      and `save_architecture` can still retry.

    What this does NOT do is tell the user the save failed. The model is handed no
    receipt, so it says nothing about a file, which reads as a document that was
    generated but not saved — true, and the closest honest thing available here.
    """
    if not (markdown and markdown.strip()):
        return ""
    user_id, session_id = get_user_id(), get_session_id()
    if not user_id or not session_id:
        logger.info(
            "design auto-save skipped — no run context (user set: %s, session set: %s)",
            bool(user_id), bool(session_id),
        )
        return ""
    filename = _architecture_filename(markdown)
    try:
        _path, url = await _write_architecture_docx(markdown, filename)
    except Exception:  # noqa: BLE001 — never let the save cost the generation
        logger.exception("design auto-save failed for %s", filename)
        return ""
    if url:
        return f"SAVED: {filename} — download: {url}"
    # Honest about the half-success: the file exists, the link does not.
    return f"SAVED: {filename} (in this run's output folder; no download link was built)"


def _with_save_receipt(receipt: str, markdown: str) -> str:
    """The tool's return value: the receipt, then the document itself.

    THE RECEIPT GOES IN FRONT, and that placement is load-bearing.
    `shared/services/orchestrator/artifacts_view.parse_design_markdown` splits the
    document on its `##` headers and DISCARDS everything before the first one, so a
    receipt at the front is dropped by the panel; a receipt appended at the end would
    be rendered inside the document's last section.

    The document is still returned whole. That return value is the only channel both
    surfaces share: the standalone WS loop accumulates it into `final_content`, which
    is what `_persist_design_artifacts` parses the eight design sections out of.
    """
    if not receipt:
        return markdown
    return (
        f"{receipt}\n"
        "(Written automatically — do NOT call save_architecture for this document "
        "again unless the user asks for a different name or a different format.)\n\n"
        f"{markdown}"
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def read_uploaded_file(file_path: str) -> str:
    """Read a local uploaded file and return its text content.

    Supports .txt, .md, .csv, .xlsx, .xls, .docx, .pptx, .pdf.
    Call this first whenever the user sends a file PATH. To read an approved project
    document by its id, use `read_document` instead — a different tool.

    NAMED APART FROM `read_document` DELIBERATELY. This agent binds both, and until the
    rename both were called `read_document`: the provider refuses a request whose tool
    names repeat, so every Design turn came back as "the model provider rejected the
    request as malformed" while other agents on the same model worked. Even where the
    API tolerated it, two readers under one name taking different arguments is a
    coin-flip for the model — the same reasoning that kept the Documentation agent out
    of the SharePoint fan-out.

    Args:
        file_path: Absolute path to the uploaded file.
    """
    broadcast_log(manager, f"Reading document: {file_path}", level="INFO")
    text = _extract_text_from_path(file_path)
    preview = text[:200].replace("\n", " ")
    broadcast_log(manager, f"Extracted {len(text)} chars from {os.path.basename(file_path)}: {preview}…", level="INFO")
    return text


@tool
async def list_design_components() -> str:
    """List the design components this agent can produce, with what each yields.

    Call this when the user asks what you can make, or asks for "the architecture" /
    "the design" without naming a component — then ask which they want. Costs nothing.
    """
    return _components.menu_text()


# ── scoped generation ──────────────────────────────────────────────────────────
#
# WHY THERE IS A `components` ARGUMENT AND WHY IT IS REQUIRED. Asked for "the HLD",
# this agent produced the whole eight-section document — the template was one block and
# the prompt said "ALL 8 sections, EVERY response". The catalogue in `components.py`
# splits the template per component; these tools generate ONLY the ones named, and a
# second call in the same session ADDS its sections to the document rather than
# replacing it, so "the HLD" then "now the DB schema" ends as one document with both.
#
# Required, not defaulted to "all": a default is exactly how a model that forgot the
# argument would hand the user everything again.

_GENERATION_SYSTEM = (
    "You are a Senior AI Solutions Architect. Produce ONLY the sections you are asked "
    "for, following their templates exactly, each opening with its exact `##` header. "
    "ALWAYS include valid Mermaid code blocks where a template requires a diagram. "
    "A DB schema is full CREATE TABLE SQL; an API contract carries request/response JSON."
)


def _tech_stack_receipt(meta: dict) -> str:
    """What the agent tells the user about the stack the sections were held to."""
    from shared.services.tech_stack import SOURCE_LABELS  # noqa: PLC0415

    lines = []
    if meta.get("tech_stack"):
        source = meta.get("tech_stack_source") or ""
        lines.append(f"Tech stack applied: {meta['tech_stack']} ({SOURCE_LABELS.get(source, source)}).")
    if meta.get("tech_stack_warning"):
        lines.append(f"Tech stack note for the user: {meta['tech_stack_warning']}")
    if meta.get("outside_stack"):
        lines.append("Tell the user: after one correction the technology stack table still names technologies "
                     "outside the project's tech stack — " + ", ".join(meta["outside_stack"])
                     + ". They are flagged in the document.")
    return ("\n".join(lines) + "\n\n") if lines else ""


async def generate_section_text(
    source_label: str, source_text: str, ids: List[str], custom_prompt: str, existing: str,
) -> "tuple[str, dict]":
    """The model call behind every generate tool: these sections, held to the project's tech stack.

    THE STACK IS CHOSEN HERE, in this call's own prompt — not in the chat, whose skills and
    instructions never reached it. When the Technology Stack section is produced, its table is
    checked against the stack; anything outside is corrected once, and what survives the
    correction is flagged in the document and reported, never dropped silently.
    Returns (sections markdown, facts about the call)."""
    from shared.services import tech_stack_store  # noqa: PLC0415
    from shared.services.tech_stack import (  # noqa: PLC0415
        annotate_stack_section, check_stack_table, correction_note, render_for_prompt,
    )

    eff = await tech_stack_store.current_project_tech_stack()
    block = render_for_prompt(eff)

    def _prompt(section_ids: List[str], extra: str, current: str) -> str:
        return (_components.build_generation_prompt(section_ids, custom_prompt=extra, tech_stack=block)
                + f"\n--- {source_label.upper()} (the source of requirements) ---\n{source_text}\n"
                + _components.existing_sections_note(current, section_ids))

    prompt = _prompt(ids, custom_prompt, existing)
    result = await _llm_generate_async(prompt, _GENERATION_SYSTEM)
    calls = 1
    violations: list = []
    if block and "stack" in ids:
        violations = check_stack_table(eff, result)
        if violations:
            broadcast_log(manager, "The technology stack table named technologies outside the project's "
                                   "tech stack — correcting it once...", level="INFO")
            extra = (custom_prompt + "\n\n" if custom_prompt else "") + correction_note(violations)
            so_far = _components.merge_sections(existing, result) if existing.strip() else result
            fixed = await _llm_generate_async(_prompt(["stack"], extra, so_far), _GENERATION_SYSTEM)
            calls += 1
            result = _components.merge_sections(result, fixed)
            violations = check_stack_table(eff, result)
        result = annotate_stack_section(result, eff, violations)
    meta = {
        "prompt_chars": len(prompt) + len(_GENERATION_SYSTEM),
        "model_calls": calls,
        "tech_stack": eff.stack.name if eff and eff.stack else None,
        "tech_stack_source": eff.source if eff else "none",
        "tech_stack_warning": eff.warning if eff else None,
        "outside_stack": [f"{v.technology} ({v.layer})" if v.layer else v.technology for v in violations],
    }
    return result, meta


async def _generate_components(
    source_label: str, source_text: str, components: List[str], custom_prompt: str,
) -> str:
    """Generate the named components from `source_text`, merge them into the session's
    document, save it, and return the receipt + the WHOLE document.

    Returns the refusal text (no model call) when a component is unknown — the agent
    relays it, which is what the user needs to hear instead of a different document.
    """
    try:
        ids = _components.resolve(components)
    except _components.UnknownComponentError as exc:
        return f"Error: {exc}"

    labels = _components.labels_for(ids)
    broadcast_log(manager, f"Generating {labels} from {source_label}...", level="INFO")
    existing = getattr(shared, "last_architecture", "") or ""
    # For the document's facts strip: where the requirements came from.
    shared.design_source = {
        "document content": "Uploaded document",
        "conversation context": "Conversation and project context",
    }.get(source_label, source_label)
    result, meta = await generate_section_text(source_label, source_text, ids, custom_prompt, existing)

    # The session's ONE document: what was there, plus what was just produced, in
    # catalogue order, a regenerated section replacing its earlier self.
    document = _components.merge_sections(existing, result) if existing.strip() else result
    shared.last_architecture = document
    mermaid_match = re.search(r"```mermaid(.*?)```", result, re.DOTALL)
    if mermaid_match:
        shared.mermaid = mermaid_match.group(1).strip()
    receipt = await _autosave_architecture(document)
    broadcast_log(manager, f"{labels} generated.", level="INFO")
    return _tech_stack_receipt(meta) + _with_save_receipt(receipt, document)


@tool
async def generate_architecture(
    document_text: str, components: List[str], custom_prompt: str = "",
) -> str:
    """Generate the named design components from uploaded document text.

    Produces ONLY the components listed — nothing else — and adds them to this
    session's design document; call it again with other components to add those.

    Args:
        document_text: Full text extracted from uploaded documents (use read_uploaded_file first).
        components: Which components to produce, e.g. ["hld"], ["db", "api"], or ["all"]
            for the full design document. See list_design_components for the ids.
        custom_prompt: Additional focus area or instructions from the user.
    """
    return await _generate_components(
        "document content", document_text, components, custom_prompt,
    )


@tool
async def generate_architecture_from_context(
    context: str, components: List[str], user_requirements: str = "",
) -> str:
    """Generate the named design components from conversation context (a BRD, PDD,
    stories, an approved document you read) without an uploaded file.

    Produces ONLY the components listed — nothing else — and adds them to this
    session's design document; call it again with other components to add those.

    Args:
        context: Complete context from the conversation (BRD, PDD, requirements, etc.).
        components: Which components to produce, e.g. ["hld"], ["db", "api"], or ["all"]
            for the full design document. See list_design_components for the ids.
        user_requirements: Specific additional instructions from the user.
    """
    if not context.strip():
        return "Error: Context cannot be empty."
    return await _generate_components(
        "conversation context", context, components, user_requirements,
    )


@tool
async def update_response(query: str, content: str, file_paths: Optional[List[str]] = None) -> str:
    """Update/refine previously generated content based on a query.

    Args:
        query: The update request or instructions from the user.
        content: The content to be updated.
        file_paths: Optional list of local file paths for additional context.
    """
    broadcast_log(manager, "Updating response...", level="INFO")
    extra_context = ""
    if file_paths:
        texts = [_extract_text_from_path(p) for p in file_paths if p]
        extra_context = "\n\n--- ADDITIONAL FILE CONTEXT ---\n" + "\n\n".join(texts)

    # An edit is held to the project's tech stack like a generation — "switch to Kafka" on a
    # stack without it answers with the not-covered marker, not a quiet change of stack.
    from shared.services import tech_stack_store  # noqa: PLC0415
    from shared.services.tech_stack import render_for_prompt  # noqa: PLC0415

    stack_block = render_for_prompt(await tech_stack_store.current_project_tech_stack())
    stack_rule = f"\n\n{stack_block}\n" if stack_block else ""

    prompt = f"""Update the content below based on the query. Preserve formatting and style.
Keep every `##` section the content already has, with its exact header, unless the
query asks to remove one; do not add sections the query does not ask for.

QUERY: {query}

CONTENT TO UPDATE:
{content}{extra_context}{stack_rule}

Return only the updated content."""
    result = await _llm_generate_async(prompt)
    broadcast_log(manager, "Response updated.", level="INFO")
    return result


@tool
async def markdowntodoc(content: str, output_path: str) -> str:
    """Convert a Markdown string to a .docx file and save it.

    Args:
        content: Markdown string to convert.
        output_path: Relative output path, e.g. 'architecture.docx'.
    """
    session_id = get_session_id()
    user_id = get_user_id()
    out_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "output")
    os.makedirs(out_dir, exist_ok=True)
    full_path = os.path.join(out_dir, os.path.basename(output_path))
    return await _markdown_to_docx(content, full_path)


@tool
async def save_architecture(filename: str = "", content: str = "") -> str:
    """Save the generated architecture document to a .docx file.

    Call this whenever the user says 'save', 'export', or 'download'.
    Always pass the full architecture markdown as 'content'.

    Args:
        filename: Output filename, e.g. 'project_architecture.docx'.
                  Defaults to 'architecture.docx' if omitted.
        content:  The full architecture markdown text to save. Required.
    """
    if not content or not content.strip():
        # Fallback to the last architecture generated this session — a weaker model
        # often cannot copy the full markdown into this argument.
        content = getattr(shared, "last_architecture", "") or ""
    if not content or not content.strip():
        return (
            "ERROR: No architecture content available. "
            "Generate the architecture first with generate_architecture_from_context, "
            "then call save_architecture again."
        )
    if not filename or not filename.strip():
        filename = "architecture.docx"
    if not filename.endswith(".docx"):
        filename += ".docx"
    broadcast_log(manager, f"Saving architecture document: {filename}", level="INFO")
    # ONE WRITE PATH, shared with the automatic save the generating tools now perform.
    # This tool used to open-code the directory, the conversion, the file_generated
    # broadcast and the artifact registration; `_design_broadcast_file` already did the
    # last two identically for every other design export. Two copies of "where a design
    # document goes" is how the automatic save and the asked-for one end up disagreeing
    # about the directory, and only one of them appears in Deliverables.
    full_path, _dl_url = await _write_architecture_docx(content, filename)
    broadcast_log(manager, f"Architecture document saved: {filename}", level="INFO")
    # Return the real download URL so the agent presents a working link (not a fabricated one).
    if _dl_url:
        return f"Saved '{filename}'. Download it here: {_dl_url}"
    return f"Saved '{filename}' to this run's output folder (no download link could be built)."


@tool
async def render_diagram_via_kroki(diagram_type: str, source: str) -> str:
    """Render any diagram as an SVG image URL via kroki.io.

    Use this for diagram types that Mermaid does not support natively, such as
    PlantUML, GraphViz (dot), BPMN, or Excalidraw. The returned URL can be
    embedded in markdown as an image: ![diagram](url)

    Args:
        diagram_type: One of: plantuml, graphviz, bpmn, excalidraw, c4plantuml,
                      ditaa, erd, pikchr, vega, vegalite, wavedrom.
        source: The raw diagram source code in the appropriate syntax.

    Returns a public kroki.io URL pointing to the rendered SVG.
    """
    import base64 as _b64
    import zlib as _zlib

    diagram_type = diagram_type.strip().lower()
    compressed = _b64.urlsafe_b64encode(
        _zlib.compress(source.encode("utf-8"), 9)
    ).decode("utf-8")
    # Use PNG so the image can be embedded directly in docx exports
    png_url = f"https://kroki.io/{diagram_type}/png/{compressed}"
    # Also provide SVG for browser rendering (higher quality)
    svg_url = f"https://kroki.io/{diagram_type}/svg/{compressed}"
    broadcast_log(manager, f"Kroki URL generated for {diagram_type}", level="INFO")
    # Return a markdown image using the PNG URL — _markdown_to_docx will fetch
    # and embed this PNG automatically when saving to .docx
    return (
        f"![{diagram_type} diagram]({png_url})\n\n"
        f"SVG (browser): {svg_url}"
    )


@tool
async def update_ado_epic_design_complete(
    project: str,
    epic_id: int,
    design_url: str,
    target_state: str = "Design Complete",
) -> str:
    """Mark the parent ADO epic as Design Complete and attach the design document URL.

    Call this after save_architecture succeeds AND the user confirms design is complete.
    Always confirm with the user before calling this tool.

    Args:
        project: ADO project name.
        epic_id: Work item ID of the parent epic.
        design_url: URL of the generated design document (from the save_architecture result).
        target_state: State to move the epic to. Default 'Design Complete'.
                      Use list_ado_states if unsure of the exact state name.
    """
    # CONSEQUENTIAL (§1.5): this moves a real epic and comments on it in front of the
    # team. The docstring above used to be the only control — "Always confirm with the
    # user before calling this tool" is a request to the model, not an enforced rule,
    # and the tool node executes whatever the model emits. Same shared rule the
    # Requirements board writes go through.
    # BOTH halves of the rule: the Architect's role AND their explicit yes on this
    # turn. The role check alone would pass every time an Architect drove the chat,
    # which is exactly who drives it — so the epic would move with nobody asked.
    from shared.authz.consequential import authorize_consequential  # noqa: PLC0415 — import cycle

    ok, why = await authorize_consequential(
        "design",
        action=f"Moving epic #{epic_id} to '{target_state}' and commenting on it",
        ask=(
            f"ask the user \"Shall I move epic #{epic_id} to '{target_state}' and post "
            f"the design document link on it?\""
        ),
    )
    if not ok:
        return why

    try:
        connector = get_connector()
    except RuntimeError:
        return (
            "No project-management board is connected for this run, so the epic could "
            "not be updated. An administrator can connect one on the Integrations page."
        )

    results = []
    try:
        await connector.write_adapter("move_item_state", project=project, item_id=epic_id, new_state=target_state)
        results.append(f"Epic #{epic_id} moved to '{target_state}'.")
    except Exception as exc:  # noqa: BLE001
        # The TYPE only. A connector error carries the instance URL and the full API
        # path, and this string goes straight into the model's context and into the
        # saved transcript. ConnectorAccessDenied is named separately because its fix
        # is an access level, not a retry.
        name = type(exc).__name__
        results.append(
            f"Could not move the epic state ({name})."
            + (" This project's design stage does not have write access to its board;"
               " an administrator can change that on the Integrations page."
               if name == "ConnectorAccessDenied"
               else f" Check that '{target_state}' is a real state on this board.")
        )

    try:
        comment = f"Design complete. Architecture document: {design_url}"
        await connector.write_adapter("add_comment", project=project, item_id=epic_id, comment=comment)
        results.append(f"Comment with design document link added to #{epic_id}.")
    except Exception as exc:  # noqa: BLE001
        results.append(f"Could not add the comment ({type(exc).__name__}).")

    return "\n".join(results)


@tool
async def write_design_artifact(
    run_id: str,
    hld: str = "",
    lld: str = "",
    api_contracts: str = "",
    database_schema: str = "",
    c4_diagram_url: str = "",
    security_checklist: str = "",
) -> str:
    """Persist the design artifact to the database and notify the orchestrator.

    Call this after the user confirms the design is complete to advance the
    pipeline to the development stage.

    Args:
        run_id: Pipeline run identifier (provided in the system context).
        hld: High-level design section text.
        lld: Low-level design section text.
        api_contracts: API contract section text.
        database_schema: Database schema section text.
        c4_diagram_url: Optional URL of the rendered C4 diagram.
        security_checklist: OWASP Top-10 security design checklist section text.
    """
    from shared.services.artifact_service import write_and_notify
    from shared.models.artifacts import DesignArtifact

    artifact = DesignArtifact(
        hld=hld or None,
        lld=lld or None,
        api_contracts=api_contracts or None,
        database_schema=database_schema or None,
        c4_diagram_url=c4_diagram_url or None,
        security_checklist=security_checklist or None,
    )
    await write_and_notify(run_id, "design", artifact.model_dump())
    return f"Design artifact persisted for run {run_id}"


# ── Downloadable-artifact helpers (docx already handled by save_architecture) ─────

def _design_output_dir() -> str:
    out = os.path.join(_FILES_DIR, str(get_user_id()), "orchestrator", str(get_session_id()), "output")
    os.makedirs(out, exist_ok=True)
    return out


async def _design_broadcast_file(filename: str, path: str) -> str:
    """Broadcast a file_generated event for a produced file and return its download URL."""
    try:
        user_id = get_user_id()
        session_id = get_session_id()
        size = os.path.getsize(path) if os.path.exists(path) else 0
        url = f"{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{filename}"
        await manager.broadcast({
            "type": "file_generated",
            "session_id": session_id,
            "filename": filename,
            "url": url,
            "file_size": size,
            "agent_name": "Design Agent",
        })
        try:
            from shared.services.chat_artifacts import register_generated_file  # noqa: PLC0415
            await register_generated_file(filename, path, url, stage="design")
        except Exception:  # noqa: BLE001 — best-effort; never block generation
            logger.debug("design register_generated_file failed", exc_info=True)
        return url
    except Exception as exc:  # noqa: BLE001 — surfacing is best-effort
        logger.warning("design file_generated broadcast failed: %s", exc)
        return ""


def _design_download_bytes(url: str) -> Optional[bytes]:
    try:
        import httpx as _httpx  # noqa: PLC0415
        r = _httpx.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


@tool
async def generate_ppt(content: str, output_path: str) -> str:
    """Generate a PowerPoint (.pptx) deck from markdown and make it downloadable from chat —
    e.g. an architecture review deck. '#'/'##' headings start slides; '- ' lines are bullets.

    Args:
        content: Markdown-style deck content (headings = slides, bullets = points).
        output_path: Filename, e.g. 'architecture_overview.pptx'.
    """
    try:
        from pptx import Presentation  # noqa: PLC0415
    except ImportError:
        return "Error: PowerPoint generation needs python-pptx — run `pip install python-pptx` on the backend."
    filename = os.path.basename(output_path) or "presentation.pptx"
    if not filename.lower().endswith(".pptx"):
        filename += ".pptx"
    dest = os.path.join(_design_output_dir(), filename)

    def _build() -> None:
        prs = Presentation()
        slides: List[tuple] = []
        cur_title: Optional[str] = None
        cur_bullets: List[str] = []
        for raw in content.splitlines():
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                if cur_title is not None:
                    slides.append((cur_title, cur_bullets))
                cur_title, cur_bullets = s.lstrip("#").strip(), []
            elif s.startswith(("- ", "* ")):
                cur_bullets.append(s[2:].strip())
            else:
                cur_bullets.append(s)
        if cur_title is not None:
            slides.append((cur_title, cur_bullets))
        if not slides:
            slides = [("Presentation", [content.strip()[:400]])]
        ft, fb = slides[0]
        ts = prs.slides.add_slide(prs.slide_layouts[0])
        ts.shapes.title.text = ft
        if fb and len(ts.placeholders) > 1:
            ts.placeholders[1].text = fb[0]
        for title, bullets in slides[1:]:
            sl = prs.slides.add_slide(prs.slide_layouts[1])
            sl.shapes.title.text = title
            tf = sl.placeholders[1].text_frame
            tf.clear()
            for i, b in enumerate(bullets or [""]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = b
        prs.save(dest)

    await asyncio.get_event_loop().run_in_executor(None, _build)
    url = await _design_broadcast_file(filename, dest)
    tail = f" Download it here: {url}" if url else ""
    return f"Created PowerPoint deck '{filename}'.{tail}"


@tool
async def generate_diagram(diagram_type: str, source: str, output_path: str = "") -> str:
    """Render a diagram as a downloadable PNG file (via kroki.io) the user can save from chat.
    Use this when the user wants to DOWNLOAD an image; use render_diagram_via_kroki when you
    only need to EMBED the diagram inline in the architecture document.

    Args:
        diagram_type: mermaid, plantuml, graphviz, c4plantuml, erd, bpmn, excalidraw, ...
        source: Diagram source code in that syntax.
        output_path: Optional filename, e.g. 'component_diagram.png'.
    """
    import base64 as _b64  # noqa: PLC0415
    import zlib as _zlib  # noqa: PLC0415

    dtype = diagram_type.strip().lower()
    compressed = _b64.urlsafe_b64encode(_zlib.compress(source.encode("utf-8"), 9)).decode("utf-8")
    png_url = f"https://kroki.io/{dtype}/png/{compressed}"
    img = await asyncio.get_event_loop().run_in_executor(None, _design_download_bytes, png_url)
    if not img:
        return f"Error: could not render the {dtype} diagram — check the type/syntax."
    filename = os.path.basename(output_path) or f"diagram_{dtype}.png"
    if not filename.lower().endswith(".png"):
        filename += ".png"
    dest = os.path.join(_design_output_dir(), filename)
    with open(dest, "wb") as fh:
        fh.write(img)
    url = await _design_broadcast_file(filename, dest)
    tail = f" Download it here: {url}" if url else ""
    return f"Rendered {dtype} diagram '{filename}'.{tail}"



@tool
async def save_architecture_pdf(content: str = "", filename: str = "architecture.pdf") -> str:
    """Save the architecture document as a PDF instead of a .docx.

    Use when the user asks for a PDF. For Word use save_architecture; for slides use
    generate_ppt.

    Args:
        content: The markdown document. Omit to use the most recently generated
            architecture for this session.
        filename: Output filename, e.g. project_architecture.pdf
    """
    if not (content and content.strip()):
        # `last_architecture`, NOT `output_file`. output_file holds a FILENAME
        # (os.path.basename(docx_path)) — reading it as content produced
        # "Error: nothing to save" for a user who had a full document on screen,
        # because the fallback was looking at the wrong attribute entirely.
        content = getattr(shared, "last_architecture", "") or ""
    if not (content and content.strip()):
        return ("Error: nothing to save. Generate the architecture first "
                "(generate_architecture_from_context), or pass the content explicitly.")

    filename = os.path.basename(filename or "architecture.pdf")
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    user_id, session_id = get_user_id(), get_session_id()
    out_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "output")
    os.makedirs(out_dir, exist_ok=True)
    full_path = os.path.join(out_dir, filename)

    try:
        await _markdown_to_pdf(content, full_path, title=filename.rsplit(".", 1)[0])
    except Exception as exc:  # noqa: BLE001
        return f"Error generating the PDF ({type(exc).__name__})."

    url = await _design_broadcast_file(filename, full_path)
    return (
        f"Saved '{filename}'."
        + (f" Download it here: {url}" if url else "")
        + " It is NOT yet in the project's artifacts — ask the user whether to save it "
          "there. It is awaiting a project admin's approval."
    )


@tool
async def read_project_requirements() -> str:
    """Load this project's requirements — the stories, their descriptions and their
    acceptance criteria — from the Requirements stage.

    Call this when the user asks you to design something FOR THIS PROJECT, or refers to
    "the requirements", "the stories", "the backlog" or similar. Do NOT call it when the
    user is asking you to design something they have described themselves in the chat:
    their words are the requirement then, and loading the project's backlog on top of
    them would design the wrong system.

    Returns the requirements as text, or says plainly that the project has none.

    THIS REPLACED AN AUTOMATIC INJECTION. The Design page used to push every story into
    the agent's context before the user had typed anything, behind a "From requirements
    / Freeform" toggle that defaulted to on. That fed the agent whatever the board held
    — Epics and project-setup Tasks included — and it designed a system for them. Making
    it a tool is what `_build_session_context` argued for all along: context the model
    chooses to load, not an injection it cannot decline.
    """
    from config.context_broker import build_context_for_project  # noqa: PLC0415
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    project_id, tenant_id = get_project_id(), get_tenant_id()
    if not project_id or not tenant_id:
        # A standalone conversation outside any project. Say so rather than returning
        # an empty string the model would read as "the project has no requirements".
        return (
            "This conversation is not attached to a project, so there are no stored "
            "requirements to read. Design from what the user has described."
        )

    context = await build_context_for_project(project_id, tenant_id, "design")
    if not context:
        return (
            "This project has no requirements recorded yet. Design from what the user "
            "has described, or ask them what the system needs to do."
        )
    return context



@tool
async def export_document(content: str = "", filename: str = "architecture.docx") -> str:
    """Export content as a Word (.docx), PDF (.pdf), Excel (.xlsx), Markdown (.md) or text file.

    ONE tool for every document format — the format comes from the FILENAME EXTENSION,
    so "as a PDF" means filename="something.pdf".

        Word         architecture.docx
        PDF          architecture.pdf
        Excel        api_contract.xlsx  (exports the MARKDOWN TABLES, one sheet per table)
        Markdown     architecture.md

    Args:
        content: The markdown to export. Omit to use the most recently generated
            architecture document for this session.
        filename: Output filename INCLUDING the extension you want.

    For diagrams/images use generate_diagram or render_diagram_via_kroki; for slides
    use generate_ppt. save_architecture / save_architecture_pdf remain for the plain
    architecture document; this tool exports ANY content in ANY of these formats.
    """
    from shared.tools.doc_export import (  # noqa: PLC0415
        export_result_message,
        normalise_filename,
        render_document,
        supported_list,
    )

    if not (content and content.strip()):
        # `last_architecture`, NOT `output_file`. output_file holds a FILENAME
        # (os.path.basename(docx_path)) — reading it as content produced
        # "Error: nothing to save" for a user who had a full document on screen,
        # because the fallback was looking at the wrong attribute entirely.
        content = getattr(shared, "last_architecture", "") or ""
    if not (content and content.strip()):
        return ("Error: nothing to export. Generate the architecture first "
                "(generate_architecture_from_context), or pass the content explicitly.")

    name = normalise_filename(filename, "architecture.docx")
    user_id, session_id = get_user_id(), get_session_id()
    out_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "output")
    os.makedirs(out_dir, exist_ok=True)
    full_path = os.path.join(out_dir, name)

    try:
        # Word and PDF are the DESIGNED document (title band, facts strip, palette);
        # the generic renderer is Word's defaults and would not match what
        # save_architecture produces. The other formats have no design to carry.
        ext = os.path.splitext(name)[1].lower()
        if ext == ".docx":
            await _markdown_to_docx(content, full_path)
        elif ext == ".pdf":
            await _markdown_to_pdf(content, full_path, title=name.rsplit(".", 1)[0])
        else:
            await render_document(content, full_path, title=name.rsplit(".", 1)[0])
    except ValueError:
        # render_document refuses an extension it has no renderer for. Its message is
        # ours, not a connector's, but the sweep in test_board_write_failures cannot
        # tell those apart from source text — and the filename already names the
        # offending extension, so interpolating the exception adds nothing.
        return f"Error: '{name}' has an unsupported extension. Supported: {supported_list()}"
    except Exception as exc:  # noqa: BLE001
        return f"Error generating '{name}' ({type(exc).__name__}). Supported: {supported_list()}"

    url = await _design_broadcast_file(name, full_path)
    extras = []
    if name.lower().endswith(".xlsx"):
        extras.append("Excel exports contain the document's TABLES, one sheet each.")
    return export_result_message(name, url, extras)


try:
    from shared.tools.project_documents import make_document_tools  # noqa: PLC0415

    # Bound to this agent's stage: it decides what "its own agent" means for an
    # approved-but-uncovered document, and it is what the evidence trail records as
    # the reader. Never a tool argument — a prompt could then claim another agent.
    _DOCUMENT_TOOLS = make_document_tools("design")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _DOCUMENT_TOOLS = []

try:
    from shared.tools.sharepoint_artifacts import make_sharepoint_tools  # noqa: PLC0415

    # Publishes APPROVED documents only, and cannot delete anything from the library —
    # see shared/tools/sharepoint_artifacts. `agent_id` and `stage` are bound here and
    # never taken from a tool argument, or a prompt could claim another agent's grant.
    _SHAREPOINT_TOOLS = make_sharepoint_tools(agent_id="design", stage="design")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _SHAREPOINT_TOOLS = []

try:
    from shared.tools.confluence_artifacts import make_confluence_tools  # noqa: PLC0415

    # THE SAME CAPABILITY, FOR THE OTHER DOCUMENT SYSTEM. Asked to publish an approved
    # document to Confluence, this agent used to answer that the platform "only
    # publishes to SharePoint" — truthfully, because the Confluence tools existed in
    # the connector and were bound to no agent but Documentation. Bound the same way as
    # SharePoint: agent and stage fixed here, never taken from a tool argument.
    _CONFLUENCE_TOOLS = make_confluence_tools(agent_id="design", stage="design")
except Exception:  # noqa: BLE001 — a missing optional tool must not break the agent
    _CONFLUENCE_TOOLS = []
    logger.warning("Design agent: Confluence document tools unavailable")

tools = [
    read_uploaded_file,
    # The project's requirements, ON DEMAND. Replaced the Design page's automatic
    # injection, which pushed every board item into context before the user had typed.
    read_project_requirements,
    # What can be produced — the answer to a generic ask, from the catalogue.
    list_design_components,
    generate_architecture,
    generate_architecture_from_context,
    update_response,
    # markdowntodoc intentionally NOT bound — it's redundant with save_architecture
    # (which converts markdown→docx) and a weaker model fixates on it, calling it with
    # empty content and looping. Removing it forces the correct
    # generate_architecture_from_context → save_architecture path.
    save_architecture,
    # PDF output, and the explicit save the user is asked for before anything is
    # written to the project's shared artifact storage.
    save_architecture_pdf,
    export_document,
    generate_ppt,
    generate_diagram,
    render_diagram_via_kroki,
    update_ado_epic_design_complete,
    write_design_artifact,
    analyze_existing_system,
    run_spectral_lint,
    validate_database_schema,
    # Figma — a design that already exists is a better input than a described one.
    list_figma_frames,
    read_figma_design,
    export_figma_frames,
    *_DOCUMENT_TOOLS,
    *_SHAREPOINT_TOOLS,
    *_CONFLUENCE_TOOLS,
]


# ── System prompt ──────────────────────────────────────────────────────────────

# WHY "ACT, DON'T NARRATE" IS WORDED THE WAY IT IS. It used to end with "if the
# structured pipeline context already contains requirements / user stories ... call
# generate_architecture_from_context immediately". The presence of context WAS the
# instruction, so a user who opened the Design page and typed "hi" received a complete
# eight-section architecture document they had never asked for. The Design page threads
# the project's stories into pipeline_context by default ("From requirements" mode), so
# this fired on the first message of every project that had requirements.
#
# The anti-narration rule itself is worth keeping — a model that says "I'll generate
# that now" and then stops is a real failure. The trigger just has to be the user
# asking. See tests/test_design_greeting_does_not_generate.py.
DESIGN_SYS_MESSAGE = """\
You are the Design & Architecture Agent — powered by Claude. You transform
requirements, BRDs, PDDs, and user stories into enterprise-grade design components —
the ones the user asks for, and only those.

── WHAT YOU CAN PRODUCE, AND ONLY WHAT IS ASKED (CRITICAL) ───────────────────
Your design components (ids in backticks are what the generation tools take):

{components_roster}

- A GENERIC ASK — "create the architecture", "design this", "make the design
  document" — is NOT a request for everything. Call `list_design_components`, show the
  user that list, and ask which they want; offer "the full design document" as one of
  the choices. Do not generate until they choose.
- A SPECIFIC ASK produces exactly what was named: "the HLD" → `components=["hld"]`;
  "DB schema and API contract" → `components=["db", "api"]`; "the full design
  document" / "everything" → `components=["all"]`. Never add a component that was
  not asked for — no executive summary, deployment plan, risks or security review
  unless requested or part of the full document.
- ADDING LATER: "now add the DB schema" → call the generation tool again with just
  `["db"]`. The tool merges it into this session's document — the earlier sections are
  kept, and the document is re-saved with all of them. Regenerating a component the
  document already has replaces that section only.
- If you cannot produce what was named, the tool tells you what exists; relay that and
  ask. Never substitute a different component.

── ACT, DON'T NARRATE (CRITICAL) ─────────────────────────────────────────────
When the user ASKS you to generate, design, or save, you MUST emit the tool call
in the SAME response — do NOT reply with only a preamble like "I'll generate it
now" and then stop. A turn that announces an action without calling the tool is a
failure.

THE USER ASKING IS THE TRIGGER — NOT THE PRESENCE OF CONTEXT. Structured pipeline
context (requirements / user stories) is SOURCE MATERIAL for when you are asked to
design. It is not an instruction, and it is not consent. If the user greets you,
asks a question, or says something you cannot read as a request to produce a design,
REPLY TO WHAT THEY SAID. Say briefly what you can see (e.g. "I can see N stories
from Requirements") and ask what they want built.

When you ARE asked, use the context you have: pass those stories as `context`, and
never ask the user to re-supply requirements that are already in front of you.

── THE PROJECT'S REQUIREMENTS ARE A TOOL CALL, NOT SOMETHING YOU ARE HANDED ──────
You do NOT automatically receive this project's stories. Call
`read_project_requirements` to load them.

CALL IT when the user asks you to design something FOR THIS PROJECT, or refers to
"the requirements", "the stories", "the backlog", "what we captured" or similar.
Call it BEFORE designing in that case — designing from memory when stored
requirements exist produces a system nobody asked for.

DO NOT CALL IT when the user describes what they want in the chat. Their words ARE
the requirement then. Loading the project's backlog on top of a description the user
just gave you designs the wrong system — and a project's board holds Epics and
setup Tasks alongside real stories, none of which are things to design.

DO NOT CALL IT on a greeting. "hi" is not a request to design anything.


── SAVING DOCUMENTS TO THE PROJECT (AUTOMATIC, AS A DRAFT) ───────────────────────
Every document you generate is recorded in the project's Documents automatically, as a
DRAFT. You do NOT ask whether to save it, and there is no tool to call.
- The generating tool has ALREADY written the .docx and already handed you its
  download link on a "SAVED:" line. Creating the file is not something you ask for.
- After generating a document, tell the user it is ready and give the download link.
- Then say it is in the project's Documents as a draft: raising it for approval is the
  user's next step, and a project admin decides whether it joins the project's shared
  record.
- Do NOT claim it has been "added to the project" or "saved to artifacts" — it is
  waiting on someone else's decision, and saying otherwise sets the wrong expectation.
- The download link works immediately either way.

── FILE FORMATS ──────────────────────────────────────────────────────────────────
export_document produces EVERY document format — the format comes from the FILENAME
EXTENSION you pass:
  Word         export_document(filename="report.docx")
  PDF          export_document(filename="report.pdf")
  Excel        export_document(filename="matrix.xlsx")  — exports the document's
               TABLES, one sheet per table (not prose)
  Markdown     export_document(filename="notes.md")
  Diagram      generate_diagram
  Slides       generate_ppt
Never substitute a format silently. If the user asks for PDF, pass a .pdf filename.
If they ask for a format not listed here, say which ones are available.

── SCOPE BOUNDARY (CRITICAL) ─────────────────────────────────────────────────
You are ONLY responsible for design and architecture artifacts.
- If asked about code, implementation, or development: say "That's handled by the
  Development Agent." then call write_design_artifact to persist your output.
- If asked about test plans or QA: say "That's handled by the Testing Agent."
  then call write_design_artifact to persist your output if design is complete.
- If asked for a REQUIREMENTS artifact — a BRD, PDD, MoM, risk register, user
  stories, acceptance criteria, or a requirements document of any kind — say
  "That's handled by the Requirements Agent, on the Requirements page." and STOP.
  You have no tool that produces any of them.

NEVER SUBSTITUTE A DIFFERENT ARTIFACT FOR THE ONE ASKED FOR. If you cannot produce
what the user named, say which agent can and stop. Producing an architecture
document because a BRD was requested wastes minutes of the user's time, spends
their tokens, and answers a question nobody asked — the user has to read the whole
thing to discover it is not what they wanted. "I can't do that here, the
Requirements Agent can" is a better answer than a document.

── ACCURACY & GROUNDING (READ FIRST — CRITICAL FOR DEMO) ────────────────────
These rules prevent hallucination. Every section of the architecture document
must be derived from the requirements provided — not invented.

1. REQUIREMENTS FIRST. Before generating architecture, you MUST have one of:
   - Uploaded file(s) the user provided, OR
   - A requirements payload injected from the Requirements Agent, OR
   - The user's direct description of what to build.
   If none of these exist, ask: "Could you share the requirements or user
   stories before I start? This ensures the design matches your actual system."

2. NO SILENT INVENTION. If a requirement, entity, endpoint, or business rule
   is not in the source material, do NOT silently include it. Either:
   - Ask the user first, or
   - Include it tagged as: > ⚠️ **[ASSUMPTION]** — not specified in requirements.
     Please confirm or correct this before development begins.

3. API ENDPOINTS must map to user stories. For each endpoint in the API
   CONTRACT section, include a one-line annotation:
   "Implements: [User Story / Feature name from requirements]"
   Never define endpoints for features not mentioned in requirements.

4. DATABASE TABLES must map to domain entities from requirements. If you name
   a table or column that wasn't in the source, tag it [ASSUMPTION].

5. IF CONTEXT IS THIN → ASK, DON'T GUESS.
   If requirements are vague (e.g. "build a leave management app" with no
   stories), ask 2–3 targeted questions before calling generate_architecture:
   - "What are the main user roles?"
   - "What are the key actions each role performs?"
   - "Are there any existing systems this must integrate with?"

── WORKFLOW ──────────────────────────────────────────────────────────────────
0. EXISTING-SYSTEM ANALYSIS (do this FIRST when a repo path is available):
   call analyze_existing_system(repo_path) to detect the current frameworks,
   language mix, and API/DB files. Design to EXTEND the existing system — reuse
   its stack and patterns instead of proposing a greenfield rewrite. If no repo
   path is available, skip this step.
0b. EXISTING DESIGN (do this when the user mentions Figma or gives a figma.com link):
   call list_figma_frames(file_url) to get the screen inventory, then
   read_figma_design(file_url, node_ids) on the screens that matter. Design to the
   screens that EXIST — name the real components and reuse the real copy instead of
   inventing a UI the designers did not draw. Use export_figma_frames(node_ids) only
   when the user wants images embedded in the document; its URLs expire in ~30 days,
   so never present them as permanent. If Figma is not connected, the tools say so —
   carry on from the written requirements rather than stopping.
1. If the user provides file paths → call read_uploaded_file for EACH file first.
2. Pass extracted text to generate_architecture, with `components` naming ONLY what
   the user asked for.
3. If no files → use generate_architecture_from_context with conversation context,
   again with `components` naming only what was asked for.
4. VALIDATION LOOPS (run after generation, only for components that were produced):
   • API CONTRACT — save the OpenAPI YAML to a file, then call
     run_spectral_lint(spec_path). If it returns findings with severity "error",
     fix the spec and re-lint until clean (or "unavailable"). Advisory only —
     proceed if the tool is unavailable.
   • DATABASE SCHEMA — call validate_database_schema(ddl) on the CREATE TABLE
     block. For each issue (e.g. missing_primary_key), correct the DDL and
     re-validate. Advisory only — proceed if unavailable.
   These tools degrade gracefully — never block the design if one is unavailable.
5. After generation + validation, ask if the user wants to save as .docx (save_architecture).
6. On "save", "export", "download" → call save_architecture with ONLY the filename.
   NEVER pass 'content' — the system caches the last generated architecture automatically.
   Example: save_architecture(filename="project_architecture.docx")
7. On update/refinement requests → call update_response.
   When updating, preserve every section the document already has — never drop a
   section when refining only one part of the document — and add none.

── OUTPUT FORMAT (CRITICAL — the platform parses these exact headers) ─────────
Each component is one `##` section with its EXACT header (uppercase, no trailing
punctuation): OVERVIEW, HIGH-LEVEL DESIGN, LOW-LEVEL DESIGN, C4 ARCHITECTURE
DIAGRAMS, API CONTRACT, DATABASE SCHEMA, ARCHITECTURE DECISION RECORDS,
TECHNOLOGY STACK, SECURITY DESIGN CHECKLIST. The document holds only the sections
that were asked for; the platform splits on these headers to show each one.

── WHAT EACH COMPONENT CONTAINS ──────────────────────────────────────────────
The generation tools carry the full template for every component, so you never
write a section by hand. For your own understanding and for answering the user:

{components_detail}

Rules that apply WHEN a component is produced:
- HIGH-LEVEL DESIGN includes a high-level architecture diagram (Mermaid graph/flowchart).
- LOW-LEVEL DESIGN includes BOTH a component/class diagram AND a sequence diagram.
- C4 ARCHITECTURE DIAGRAMS shows all three levels (context, container, component).
- DATABASE SCHEMA includes both an ER diagram (Mermaid erDiagram) AND CREATE TABLE SQL.
- API CONTRACT includes the full OpenAPI 3.0 YAML.
- SECURITY DESIGN CHECKLIST assesses all 10 OWASP categories against THIS design.

── DIAGRAM RENDERING ─────────────────────────────────────────────────────────
Primary: Use Mermaid (```mermaid blocks) for all standard diagrams — flowcharts,
sequence, ER, class, C4Context/C4Container/C4Component.

Fallback (render_diagram_via_kroki): Use when the user asks for diagram types
Mermaid cannot render natively:
- PlantUML UML diagrams         → diagram_type="plantuml"
- C4-PlantUML (text C4 syntax)  → diagram_type="c4plantuml"
- GraphViz dot graphs           → diagram_type="graphviz"
- BPMN process flows            → diagram_type="bpmn"
The tool returns a markdown image tag — include it directly in your response.

── POST-TOOL RESPONSE ────────────────────────────────────────────────────────
generate_architecture and generate_architecture_from_context SAVE the document
themselves. Their result opens with a "SAVED:" line naming the .docx and carrying
its download link. Quote that link exactly as it was given to you and never invent
one — a made-up /generated/ URL is indistinguishable from a real one and 404s.

On the Design page the full document has already been streamed to the user in real
time as it was generated, so Do NOT repeat or summarise the tool output at length —
sending it back shows the same document twice. Through the Orchestrator the user
sees only what YOU say, so your reply has to stand on its own there.

Reply in 2-4 sentences: the filename, the download link exactly as returned, the
## section headings the document contains, and that it is awaiting a project
admin's approval. NEVER offer to save it and NEVER ask whether to save it — it is
already saved, and an offer is how a turn ends with no document anywhere.

── MERMAID THAT ACTUALLY RENDERS ─────────────────────────────────────────────
A diagram that fails to parse shows the reader an error box instead of a diagram,
so it is worse than a simpler diagram that works.

USE ONLY these four types — they are the ones the templates use and the ones the
viewer renders reliably:
    graph / flowchart      classDiagram      sequenceDiagram      erDiagram

NEVER use C4Context, C4Container, C4Component, mindmap, timeline, quadrantChart or
block-beta. Mermaid's C4 support is experimental and fails often; the C4 sections
above are drawn with `graph TB` for exactly that reason — follow those templates
rather than reaching for a C4-specific syntax.

Rules that prevent most parse failures:
- Put EVERY node label in double quotes: A["Order Service"], never A[Order Service].
  An unquoted label containing a bracket, parenthesis, comma, colon or slash is the
  single most common cause of a broken diagram.
- Never put a raw " inside a label. Use a different word.
- In classDiagram, keep method signatures simple: +save(), +findById(). Avoid
  generics (List~T~), nested parentheses and return-type annotations.
- One statement per line. Do not put a comment on the same line as a statement.
- Keep every diagram under ~25 nodes; split it rather than growing it.

── ABSOLUTE RULES ────────────────────────────────────────────────────────────
- NEVER write architecture as a plain-text reply. ALWAYS call a tool first.
- NEVER say you cannot access files — always call read_uploaded_file for a path, or read_document for an approved project document.
- PRODUCE ONLY THE COMPONENTS THE USER ASKED FOR. The rules under "what each
  component contains" apply to a component when it is produced — they never make a
  component mandatory. A request for the HLD yields the HLD and nothing else.
- Use descriptive filenames when saving: 'leave_mgmt_hld.docx'.
- NEVER invent API endpoints, database tables, business rules, or user roles
  that are not present in the requirements. Tag any necessary assumption with
  ⚠️ [ASSUMPTION] so the user can confirm before development starts.
- IF requirements context is empty or has fewer than 3 user stories/features,
  ask the user for more detail before generating — do not produce a generic
  architecture diagram that doesn't match their actual system.

── ADO WRITE-BACK ─────────────────────────────────────────────────────────────
After the user confirms the design is complete AND a DOCX has been saved:
1. Call update_ado_epic_design_complete(project, epic_id, design_url, target_state)
   - project: ADO project name (ask the user if not known)
   - epic_id: work item ID of the parent epic (ask the user if not known)
   - design_url: the URL returned by save_architecture
   - target_state: "Design Complete" (default) — confirm real state name if unsure
2. The tool moves the epic state and adds a comment with the document link.
3. Always confirm with the user before calling this tool.
4. If the user hasn't saved a document yet, call save_architecture first.

── PIPELINE HANDOFF ─────────────────────────────────────────────────────────
Trigger: "start coding", "develop it", "write the code", "build it",
"proceed to development", "next step" (after design is confirmed complete).
Response: one sentence confirming design is complete, then call write_design_artifact
with the run_id from the session context and ALL relevant sections populated,
INCLUDING security_checklist (the OWASP Top-10 review).

── FINAL REMINDER (HIGHEST PRIORITY) ─────────────────────────────────────────
When asked to produce a NAMED component (or "the full design document"), follow this
EXACT tool sequence:
  STEP 1 — call `generate_architecture_from_context` with the requirements passed as
           the `context` argument (a single string) and `components` naming ONLY what
           was asked for (e.g. ["hld"], or ["all"] for the full document). This
           returns the session's design document with the new section(s) in it AND
           writes the .docx; the result opens with the "SAVED:" line carrying the
           filename and the download link.
  STEP 2 — reply with that filename, that link, and the sections the document now
           holds. There is no second save to make.
When the ask names NO component ("design this", "create the architecture"), STEP 1
is instead `list_design_components` and a question — not a generation.
Call `save_architecture`, `save_architecture_pdf` or `export_document` ONLY when the
user asks for a different filename or a different format — calling one on the document
you just generated writes the same document a second time under a second name, and the
user then has two files and no idea which is current. NEVER call `markdowntodoc`
directly, and NEVER call any save tool with an empty `content`. NEVER end a turn with
only "I'll generate it now" and no tool call: that is a hard failure. Do not ask for
requirements you already have.
"""

# The component roster is RENDERED from the catalogue, in two places, so the prompt
# cannot name a component the generation tools cannot produce. `.replace`, not
# `.format`: the prompt is full of literal braces.
DESIGN_SYS_MESSAGE = (
    DESIGN_SYS_MESSAGE
    .replace("{components_roster}", _components.roster_text())
    .replace(
        "{components_detail}",
        "\n".join(f"- {c.label}: {c.yields}." for c in _components.COMPONENTS),
    )
)

# Append the shared, agent-agnostic MCP provenance note (see shared/tools/mcp_runtime).
DESIGN_SYS_MESSAGE = DESIGN_SYS_MESSAGE + MCP_TOOLS_PROMPT_NOTE


# ── LangGraph workflow ─────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    tenant_id: str
    model_id: str | None
    offering_id: str | None
    # BYOK-resolved model carried through state so the tools node can re-establish it
    # (LangGraph runs nodes in separate task contexts, so the contextvar set in the
    # agent node does not reliably reach the tools that call _llm_generate_async).
    resolved_model: Any


# Per-alias orchestrator cache — keyed by (alias, model) to avoid re-constructing
# ChatLiteLLM on every invocation while still supporting per-tenant BYOK keys (Pitfall 4).
_ORCHESTRATOR_CACHE: dict[tuple[str, str, str, str], object] = {}


def _build_orchestrator(model: str, litellm_provider: str, api_key: str,
                        base_url: str | None, alias: str) -> object:
    """Build (or return cached) ChatLiteLLM orchestrator. Direct-provider BYOK call.
    Cached by (alias, model); alias is non-secret (SC#5)."""
    # The credential is part of what this instance IS, so it belongs in the key —
    # the alias alone is stable across a key rotation and kept handing back a
    # client built with the old secret for the life of the process. See
    # shared/services/model_resolver.credential_fingerprint.
    from shared.services.model_resolver import credential_fingerprint  # noqa: PLC0415
    cache_key = (alias, model, credential_fingerprint(api_key, base_url), base_url or "")
    if cache_key in _ORCHESTRATOR_CACHE:
        return _ORCHESTRATOR_CACHE[cache_key]
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    from shared.services.chat_litellm import ChatLiteLLM
    from shared.services.model_resolver import temperature_kwargs  # noqa: PLC0415

    from shared.services.model_resolver import (  # noqa: PLC0415
        litellm_key_kwargs,
        temperature_kwargs,
    )
    instance = ChatLiteLLM(
        model=model,
        custom_llm_provider=litellm_provider,
        api_base=base_url,
        api_key=api_key,
        # The BYOK key must be the one litellm actually uses; without this the
        # provider-specific field defaulted from the environment wins. See
        # shared/services/model_resolver.litellm_key_kwargs.
        **litellm_key_kwargs(litellm_provider, api_key),
        max_tokens=8192,
        # Omitted entirely for gpt-5-family models, which reject any temperature but
        # their default — see temperature_kwargs.
        **temperature_kwargs(model, 0.2),
        # 0, not langchain_litellm's own default -- guarded_completion
        # (shared/services/model_call_wrapper.py) already retries the whole
        # ainvoke call; ChatLiteLLM's own tenacity retry underneath it also
        # retries on RateLimitError, uncoordinated with the outer loop. See
        # dev_agent.py::_build_llm's identical fix and "desicions and
        # issues.txt" Issue 14 for the live evidence.
        max_retries=0,
        # Stream tokens so the copilot shows the design agent's replies live.
        streaming=True,
    )
    _ORCHESTRATOR_CACHE[cache_key] = instance
    return instance


def _with_tech_stack_note(messages: list, block: str) -> list:
    """The project's tech stack joined to the system message sent THIS turn, never stored.

    The chat's SystemMessage is built once, on a session's first turn; a stack chosen or
    changed afterwards would never reach it. Joining (not adding a second system message) keeps
    providers that accept only one system message happy."""
    if not block:
        return messages
    if messages and isinstance(messages[0], SystemMessage) and isinstance(messages[0].content, str):
        return [SystemMessage(content=f"{messages[0].content}\n\n{block}")] + list(messages[1:])
    return [SystemMessage(content=block)] + list(messages)


def _sanitize_messages(messages: list) -> list:
    """Keep tool_use / tool_result blocks paired in BOTH directions.

    The implementation moved to `shared.services.message_pairing` unchanged — the
    Development and Requirements agents need exactly this repair for exactly the same
    reason, and three copies is how one of them ends up with only half the rule (which
    is what had already happened). See that module for why the invariant matters and
    why a dangling call is stripped rather than answered with a fabricated result.
    """
    from shared.services.message_pairing import sanitize_tool_call_pairing  # noqa: PLC0415

    return sanitize_tool_call_pairing(messages)


async def agent(state: AgentState):
    from shared.services.model_resolver import resolve_model_for_run, set_resolved_model, NoModelConfiguredError, ModelNotEnabledError
    tenant_id = state.get("tenant_id", "")
    requested = state.get("model_id")
    try:
        resolved = await resolve_model_for_run(tenant_id, requested, offering_id=state.get("offering_id"))
    except (NoModelConfiguredError, ModelNotEnabledError) as e:
        logger.warning("Design agent model resolution failed (tenant=%s): %s", tenant_id, type(e).__name__)
        return {"messages": [AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in Org Settings → Model Providers."))]}
    except Exception as e:
        logger.error("Design agent model resolution error (tenant=%s): %s", tenant_id, type(e).__name__)
        # The TYPE NAME ALONE WAS THE WHOLE REPLY — a user hit an Azure rate limit
        # and was shown "Agent error: RateLimitError", which does not say it is
        # temporary, not their fault, or that another model would work now.
        # Still never str(exc): a BYOK provider error can echo the tenant key.
        from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

        return {"messages": [AIMessage(content=friendly_model_error(e))]}
    set_resolved_model(resolved)
    try:
        from shared.services import tech_stack_store  # noqa: PLC0415
        from shared.services.tech_stack import render_for_prompt  # noqa: PLC0415

        clean_messages = _with_tech_stack_note(
            _sanitize_messages(state["messages"]),
            render_for_prompt(await tech_stack_store.current_project_tech_stack()),
        )
        orch = _build_orchestrator(resolved.model, resolved.litellm_provider,
                                   resolved.api_key, resolved.base_url, resolved.alias)
        # Bind base tools + any per-run MCP tools here (not in the cached builder) so
        # run-specific MCP tools never leak across runs via the shared orchestrator cache.
        orch = orch.bind_tools(tools + get_skill_tools("design") + get_mcp_tools())
        from shared.services.model_call_wrapper import guarded_completion

        response = await guarded_completion(
            resolved, orch, clean_messages, tenant_id=tenant_id, agent_type="design",
            config={"metadata": {"user_api_key_alias": resolved.alias}},
        )
        return {"messages": [response], "resolved_model": resolved}
    except Exception as e:
        # NEVER str(exc) here — credential leakage risk (BYOK-resolved model calls
        # can surface the tenant's own API key in a provider error message).
        logger.error(
            "Design agent error (tenant=%s alias=%s): %s",
            tenant_id,
            resolved.alias,
            type(e).__name__,
        )
        # The TYPE NAME ALONE WAS THE WHOLE REPLY — a user hit an Azure rate limit
        # and was shown "Agent error: RateLimitError", which does not say it is
        # temporary, not their fault, or that another model would work now.
        # Still never str(exc): a BYOK provider error can echo the tenant key.
        from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

        return {"messages": [AIMessage(content=friendly_model_error(e))]}


_TOOL_ARG_HINTS: dict[str, str] = {
    "markdowntodoc": (
        "Do NOT call markdowntodoc directly. First call generate_architecture_from_context "
        "(pass the user stories as 'context') to produce the architecture markdown, then "
        "call save_architecture with that markdown as 'content' and a 'filename'."
    ),
    "save_architecture": (
        "save_architecture requires two arguments: "
        "'content' (the full markdown text to save) and "
        "'filename' (e.g. 'architecture.docx'). "
        "Pass the architecture markdown you just generated as 'content'."
    ),
    "generate_architecture": (
        "generate_architecture requires 'requirements_text' (the extracted requirements text). "
        "Read the source first with read_uploaded_file (a path) or read_document (a "
        "project document id), then pass the result here."
    ),
    "render_diagram_via_kroki": (
        "render_diagram_via_kroki requires 'diagram_type' (e.g. 'plantuml') and 'source' (the raw diagram source code)."
    ),
}


async def action(state: AgentState):
    # Re-establish the BYOK-resolved model in this node's context — LangGraph may run
    # the tools node in a separate task context where the agent node's contextvar set
    # didn't propagate, which would make tools calling _llm_generate_async fail
    # "No model resolved". Read the already-resolved model from state (no DB call).
    try:
        from shared.services.model_resolver import set_resolved_model, get_resolved_model
        if get_resolved_model() is None and state.get("resolved_model") is not None:
            set_resolved_model(state["resolved_model"])
    except Exception:  # noqa: BLE001 — defensive; tools degrade with a clear message
        pass
    last_message = state["messages"][-1]
    results = []
    for tc in getattr(last_message, "tool_calls", []):
        broadcast_log(manager, f"Invoking tool {tc['name']} with args {tc['args']}", level="LOGS")
        try:
            tool_to_call = next(
                (t for t in tools + get_skill_tools("design") + get_mcp_tools()
                 if t.name == tc["name"]), None
            )
            if tool_to_call is None:
                obs = f"Unknown tool: {tc['name']}"
            else:
                obs = await tool_to_call.ainvoke(tc["args"])
        except Exception as e:
            import traceback  # noqa: PLC0415
            hint = _TOOL_ARG_HINTS.get(tc["name"], "")
            # Log the REAL error (type + traceback) server-side; keep the chat message
            # concise so a huge exception string can't flood the UI as "content".
            # traceback.format_exc() already carries the exception's own message as
            # its last line — passing e too would be the same str(exc) exposure the
            # rest of this codebase avoids (credential leakage risk).
            logger.error(
                "design tool %s failed: %s\n%s",
                tc["name"], type(e).__name__, traceback.format_exc(),
            )
            short = str(e)
            if len(short) > 400:
                short = short[:400] + "… (truncated — see server logs)"
            obs = f"Tool '{tc['name']}' failed: {type(e).__name__}: {short}" + (f" — {hint}" if hint else "")
        results.append(ToolMessage(content=str(obs), tool_call_id=tc["id"], name=tc["name"]))
    return {"messages": results}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "action" if getattr(last, "tool_calls", None) else False


workflow = StateGraph(AgentState)
workflow.add_node("agent", agent)
workflow.add_node("tools", action)
workflow.add_edge("tools", "agent")
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"action": "tools", False: END})

app = workflow.compile(checkpointer=_build_checkpointer("design"))


async def run_architecture_workflow_async(messages, config=None):
    if config is None:
        config = {"configurable": {"thread_id": "default"}}
    result = None
    async for event in app.astream({"messages": messages}, config):
        result = event
    return result


def run_architecture_workflow_sync(messages, config=None):
    return asyncio.run(run_architecture_workflow_async(messages, config))
