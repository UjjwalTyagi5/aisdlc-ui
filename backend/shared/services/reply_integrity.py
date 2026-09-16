"""Checks on an agent's reply before a person acts on it.

A LINK NOBODY PRODUCED. A Requirements turn whose every tool call had failed ended
with "BRD Generated Successfully" and a download link to
example.com/download/QuickLink_BRD.docx. The model wrote the address itself. The prompt
already says "report ONLY what a tool actually returned"; a prompt is a request, and
this is the check that does not depend on the model agreeing to it.

A link is SUPPORTED when it appears in something the model was given this conversation:
a tool's result (a generated file, a board item) or a person's message (a URL they
pasted). Anything else is unsupported, and the turn says so where the reader will see
it — under the reply — rather than leaving a plausible link to a file that does not
exist.
"""
from __future__ import annotations

import re
from typing import Iterable

_URL_RE = re.compile(r"https?://[^\s<>\"'`\])]+", re.IGNORECASE)
_TRAILING = ".,;:!?*_"


def _links(text: str) -> list[str]:
    out: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(_TRAILING)
        if url and url not in out:
            out.append(url)
    return out


def unsupported_links(reply: str, sources: Iterable[str]) -> list[str]:
    """Links in `reply` that appear in none of `sources`, in reply order."""
    corpus = "\n".join(s for s in sources if s)
    return [url for url in _links(reply) if url not in corpus]


def unsupported_links_notice(links: list[str]) -> str:
    """The note appended under a reply that contains `links`."""
    listed = "\n".join(f"- {url}" for url in links)
    noun = "This link was" if len(links) == 1 else "These links were"
    return (
        "\n\n---\n"
        f"**Not a real link.** {noun} not produced by any tool in this conversation, "
        "so nothing exists at that address:\n"
        f"{listed}\n"
        "Only a link returned by a tool points to a file. If a document was meant to be "
        "generated, it was not — ask again."
    )


def message_texts(messages: Iterable[object], kinds: tuple[type, ...]) -> list[str]:
    """The string content of every message of the given classes."""
    texts: list[str] = []
    for m in messages or []:
        if not isinstance(m, kinds):
            continue
        content = getattr(m, "content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(
                str(b.get("text", "")) for b in content if isinstance(b, dict)
            )
    return texts
