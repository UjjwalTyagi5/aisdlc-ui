"""Figma tools for the Design & Architecture agent.

WHY THESE EXIST: the agent's other inputs are prose — BRDs, PDDs, user stories. When
a design already exists in Figma, describing it back to the agent in prose loses the
thing that made it worth having. These tools let the agent read the actual screen
inventory and the actual copy on those screens, so an HLD names the components that
were designed rather than components it invented.

READ-ONLY. There is no tool here that writes to Figma, because FigmaConnector declares
no write capabilities — see its module docstring.

THE SHAPE OF THE OUTPUT IS THE POINT. A Figma file at full depth is megabytes of
vector JSON: fill matrices, bezier handles, per-glyph overrides. None of that survives
contact with a context window, and none of it informs an architecture. Each tool here
returns a flattened, bounded, human-readable digest — screens, hierarchy, and text —
and drops the geometry. `read_figma_design` on a large file is a summary by design,
not a truncation by accident.

CONTRACT (matches the SharePoint tools in the documentation agent): every tool returns
a STRING, and every failure path returns a string starting "ERROR" rather than raising.
A tool that raises aborts the agent turn; a tool that explains itself lets the agent
recover or tell the user what to fix.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool

from config.ws_helper import get_tenant_id

logger = logging.getLogger(__name__)

# Node types worth naming in a digest. Everything else (VECTOR, RECTANGLE, ELLIPSE,
# LINE, BOOLEAN_OPERATION …) is drawing, not structure, and is summarized as a count.
_STRUCTURAL_TYPES = {
    "FRAME",
    "COMPONENT",
    "COMPONENT_SET",
    "INSTANCE",
    "SECTION",
    "GROUP",
    "TEXT",
}

# Hard ceilings so one enormous file cannot blow the context window regardless of what
# the caller asks for. These bound the DIGEST, not the API call.
_MAX_FRAMES_LISTED = 200
_MAX_TREE_LINES = 400
_MAX_TEXT_SNIPPETS = 150
_MAX_TEXT_CHARS = 120


async def _figma_session() -> Tuple[Optional[Any], str]:
    """Return (connector, "") for this session's tenant, or (None, reason).

    Mirrors documentation_agent's `_sharepoint_session`: resolve once, fail with a
    sentence a user can act on rather than a stack trace.
    """
    tenant_id = get_tenant_id() or ""
    if not tenant_id:
        return None, "ERROR: no tenant context in this session."
    try:
        from config.connector_factory import get_connector_for_session

        connector = await get_connector_for_session(kind="figma", tenant_id=tenant_id)
        return connector, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"ERROR reaching Figma: {type(exc).__name__}"


def _explain(exc: Exception) -> str:
    """Turn a call failure into the sentence that resolves it.

    A bare `type(exc).__name__` is what this used to return, and "HTTPStatusError" tells
    an agent nothing it can act on — it would retry the same doomed call or give up. The
    HTTP statuses Figma actually returns each have exactly one fix, so they are named.
    """
    import httpx

    from config.connectors.figma import ConnectorCredentialsMissing

    if isinstance(exc, ConnectorCredentialsMissing):
        return (
            "ERROR: Figma is not connected for this tenant. An admin can connect it "
            "on the Integrations page (Design & prototyping)."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return (
                "ERROR: Figma rejected the stored credential (HTTP %d). It may have been "
                "revoked, or it lacks file_read scope. An admin can reconnect Figma on "
                "the Integrations page." % code
            )
        if code == 404:
            return (
                "ERROR: Figma has no such file or node (HTTP 404). Check the file URL, "
                "and that the connected account can open that file."
            )
        if code == 429:
            return "ERROR: Figma is rate-limiting this tenant (HTTP 429). Try again shortly."
        return f"ERROR: Figma returned HTTP {code}."
    # export_images raises RuntimeError carrying Figma's own `err` string — that text is
    # the diagnosis, so it must survive rather than collapse to the class name.
    if isinstance(exc, RuntimeError) and str(exc):
        return f"ERROR: {exc}"
    return f"ERROR calling Figma: {type(exc).__name__}"


def _digest(
    node: Dict[str, Any], lines: List[str], text: List[str], depth: int = 0
) -> None:
    """Walk a node subtree ONCE, filling both the layer outline and the copy list.

    These were two separate recursions over the same tree — `_render_tree` and
    `_collect_text` — which walked a full-depth `get_file_nodes` response twice for two
    views of the same nodes. Each output keeps its own independent cap.

    Iterative rather than recursive: a full-depth Figma frame nests groups arbitrarily
    deep, and a recursive walk puts that depth on the Python stack. An explicit stack
    cannot blow it, and lets the walk stop the moment BOTH budgets are spent instead of
    visiting every remaining node just to return immediately.
    """
    stack: List[Tuple[Dict[str, Any], int]] = [(node, depth)]
    while stack:
        if len(lines) >= _MAX_TREE_LINES and len(text) >= _MAX_TEXT_SNIPPETS:
            return  # both budgets spent — nothing further can change the output
        current, cur_depth = stack.pop()
        node_type = current.get("type", "")
        chars = ""
        if node_type == "TEXT":
            chars = (current.get("characters") or "").strip().replace("\n", " ")

        child_depth = cur_depth
        if node_type in _STRUCTURAL_TYPES:
            if len(lines) < _MAX_TREE_LINES:
                label = f"{'  ' * cur_depth}- {current.get('name', '(unnamed)')} ({node_type})"
                if chars:
                    label += f': "{chars[:_MAX_TEXT_CHARS]}"'
                lines.append(label)
            child_depth = cur_depth + 1

        if chars and len(text) < _MAX_TEXT_SNIPPETS:
            text.append(chars[:_MAX_TEXT_CHARS])

        # Reversed so pop() yields children in document order — the outline has to read
        # top-to-bottom the way the canvas does.
        children = current.get("children") or []
        for child in reversed(children):
            if isinstance(child, dict):
                stack.append((child, child_depth))


@tool
async def list_figma_frames(file_url: str = "") -> str:
    """List the screens (top-level frames) in a Figma file, with their node ids.

    Call this FIRST when working from a Figma design — the node ids it returns are what
    read_figma_design and export_figma_frames take.

    Args:
        file_url: Figma file URL or key. Omit to use the tenant's configured
                  default file.
    """
    connector, reason = await _figma_session()
    if connector is None:
        return reason
    try:
        frames = await connector.read_adapter("list_frames", file_key=file_url)
    except ValueError as exc:
        # Unparseable key or no file configured — actionable, so say it verbatim.
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    if not frames:
        return (
            "No top-level frames found in that Figma file. It may be empty, or the "
            "screens may be nested inside groups rather than frames."
        )

    shown = frames[:_MAX_FRAMES_LISTED]
    lines = [f"{len(frames)} frame(s) in this Figma file:"]
    current_page = None
    for f in shown:
        if f.get("page") != current_page:
            current_page = f.get("page")
            lines.append(f"\nPage: {current_page or '(unnamed)'}")
        lines.append(f"  - {f.get('name', '(unnamed)')}  [{f.get('type')}]  id={f.get('id')}")
    if len(frames) > len(shown):
        lines.append(f"\n({len(frames) - len(shown)} more not shown.)")
    return "\n".join(lines)


@tool
async def read_figma_design(file_url: str = "", node_ids: str = "") -> str:
    """Read a Figma design's structure and copy as a text digest for architecture work.

    Returns the layer hierarchy (frames, components, text) and the actual copy on the
    screens — the two things that inform a design document. Vector geometry is
    deliberately omitted.

    Args:
        file_url: Figma file URL or key. Omit to use the tenant's configured default.
        node_ids: comma-separated node ids to read in full (from list_figma_frames).
                  Omit to read the whole file at shallow depth — pass ids when you
                  need the inside of a specific screen.
    """
    connector, reason = await _figma_session()
    if connector is None:
        return reason

    ids = [n.strip() for n in (node_ids or "").split(",") if n.strip()]
    try:
        if ids:
            data = await connector.read_adapter(
                "get_file_nodes", file_key=file_url, node_ids=ids
            )
            roots = [
                (payload or {}).get("document", {})
                for payload in (data.get("nodes") or {}).values()
                if isinstance(payload, dict)
            ]
            title = data.get("name", "") or "Figma nodes"
        else:
            data = await connector.read_adapter("get_file", file_key=file_url, depth=3)
            roots = [(data.get("document") or {})]
            title = data.get("name", "") or "Figma file"
    except ValueError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    roots = [r for r in roots if r]
    if not roots:
        return "ERROR: Figma returned no nodes for that request. Check the node ids."

    tree_lines: List[str] = []
    text_snippets: List[str] = []
    for root in roots:
        _digest(root, tree_lines, text_snippets)

    out = [f"# {title}"]
    if data.get("lastModified"):
        out.append(f"Last modified: {data['lastModified']}")
    out.append("\n## Structure")
    out.extend(tree_lines or ["(no structural layers found)"])
    if len(tree_lines) >= _MAX_TREE_LINES:
        out.append(f"… truncated at {_MAX_TREE_LINES} layers.")
    if text_snippets:
        out.append("\n## Copy on these screens")
        out.extend(f"- {t}" for t in text_snippets)
        if len(text_snippets) >= _MAX_TEXT_SNIPPETS:
            out.append(f"… truncated at {_MAX_TEXT_SNIPPETS} text layers.")
    if not ids:
        out.append(
            "\n(Read at shallow depth. Pass node_ids from list_figma_frames to read a "
            "specific screen in full.)"
        )
    return "\n".join(out)


@tool
async def export_figma_frames(
    node_ids: str,
    file_url: str = "",
    image_format: str = "png",
    scale: float = 2.0,
) -> str:
    """Render Figma frames to images and return their URLs, for embedding in a design doc.

    Args:
        node_ids: comma-separated node ids to render (from list_figma_frames). Required.
        file_url: Figma file URL or key. Omit to use the tenant's configured default.
        image_format: png, jpg, svg or pdf. Default png.
        scale: render scale for png/jpg, 0.01–4. Ignored for svg/pdf. Default 2.
    """
    ids = [n.strip() for n in (node_ids or "").split(",") if n.strip()]
    if not ids:
        return (
            "ERROR: node_ids is required. Call list_figma_frames first to get the ids "
            "of the screens you want rendered."
        )

    connector, reason = await _figma_session()
    if connector is None:
        return reason

    try:
        images = await connector.read_adapter(
            "export_images",
            file_key=file_url,
            node_ids=ids,
            image_format=image_format,
            scale=scale,
        )
    except ValueError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    if not images:
        return (
            "ERROR: Figma rendered no images for those node ids. Check that the ids "
            "come from this file and name renderable nodes."
        )

    lines = [f"Rendered {len(images)} frame(s) as {image_format.lower()}:"]
    lines += [f"- {nid}: {url}" for nid, url in images.items()]
    missing = [i for i in ids if i not in images]
    if missing:
        lines.append(f"({len(missing)} node(s) did not render: {', '.join(missing)})")
    # Stated because it changes what a caller should do with these: a design doc that
    # hotlinks them looks fine in review and breaks a month later.
    lines.append(
        "\nNOTE: these URLs are temporary (Figma expires them in ~30 days). Download "
        "and store any image that needs to persist in a document."
    )
    return "\n".join(lines)
