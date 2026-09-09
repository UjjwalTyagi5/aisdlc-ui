"""Every agent chat actually reads the file you attach to it.

THE FAILURE THIS PREVENTS, which is not a crash. The upload succeeds, the chip renders
under your message, the transcript keeps it — and the agent answers "I don't see any
document attached". Nothing errors, nothing is logged, and the person reads an
uninformed answer as an informed one. That is worse than having no attach button.

It has now happened three times on this platform: the Plan route dropped every
attachment until `attachment_message_contents` was extracted into
`shared/tools/document_tools.py`; the PM socket dropped them while its REST twin read
them; and the Deployment chat showed an attach button for months while
`/sdlc/agent/deployment/ws` was served by `deployment_standalone_api`, which had no
such block — the module that DID have one answers a different prefix.

That is the shape of the bug: the wiring is per module, the button is per page, and
nothing connected the two. So this connects them. For every agent chat the browser can
open, the module actually mounted at its prefix must read attachments.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.unit

#: The chat prefixes `frontend/app/api/chat/route.ts` can route a drawer to. Adding an
#: agent chat to the UI means adding it here — which is the point.
AGENT_CHAT_PREFIXES = [
    "/sdlc/agent/requirement",
    "/sdlc/agent/design",
    "/sdlc/agent/plan",
    "/sdlc/agent/development",
    "/sdlc/agent/code-review",
    "/sdlc/agent/security",
    "/sdlc/agent/testing",
    "/sdlc/agent/deployment",
    "/sdlc/agent/documentation",
]

#: The call that turns an attachment into something the model can read.
READS_ATTACHMENTS = "attachment_message_contents"


def _process_api() -> ast.Module:
    return ast.parse((ROOT / "process_api.py").read_text(encoding="utf-8-sig"))


def _router_modules() -> dict[str, str]:
    """Router variable -> the module it was imported from, as written in process_api."""
    out: dict[str, str] = {}
    for node in ast.walk(_process_api()):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


def _mounts() -> dict[str, str]:
    """Chat prefix -> router variable, read from the include_router calls themselves.

    Read rather than assumed: the whole bug is that a prefix and a module can drift
    apart, so a test that hard-coded the pairing would drift with them.
    """
    found: dict[str, str] = {}
    for node in ast.walk(_process_api()):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "include_router"):
            continue
        prefix = next(
            (k.value.value for k in node.keywords
             if k.arg == "prefix" and isinstance(k.value, ast.Constant)),
            None,
        )
        if not isinstance(prefix, str) or not node.args:
            continue
        router = getattr(node.args[0], "id", None)
        if router:
            found[prefix] = router
    return found


def _module_path(dotted: str) -> pathlib.Path:
    return ROOT.joinpath(*dotted.split(".")).with_suffix(".py")


def test_every_agent_chat_prefix_is_actually_mounted():
    """A prefix the browser routes to and nothing serves would make the test below
    vacuously true for that agent."""
    mounts = _mounts()
    missing = [p for p in AGENT_CHAT_PREFIXES if p not in mounts]
    assert not missing, f"the chat drawer routes to prefixes nothing serves: {missing}"


def test_the_module_behind_every_agent_chat_reads_attachments():
    """THE INVARIANT. Whatever module answers the prefix must read the file."""
    mounts = _mounts()
    modules = _router_modules()

    offenders: list[str] = []
    for prefix in AGENT_CHAT_PREFIXES:
        router = mounts[prefix]
        dotted = modules.get(router)
        assert dotted, f"{router} is mounted at {prefix} but never imported"
        path = _module_path(dotted)
        assert path.is_file(), f"{dotted} does not resolve to a file"
        if READS_ATTACHMENTS not in path.read_text(encoding="utf-8-sig", errors="replace"):
            offenders.append(f"{prefix} -> {dotted}")

    assert not offenders, (
        "These agent chats accept an attachment and never read it:\n  "
        + "\n  ".join(offenders)
        + f"\n\nCall {READS_ATTACHMENTS}(attachment_paths_from_context(...)) on the "
          "turn's messages, in the SOCKET handler — that is the one the drawer uses."
    )


def test_the_socket_handler_is_the_one_that_reads_them():
    """Reading them in the REST twin is not reading them.

    The PM agent had exactly this: `chat()` extracted attachments, `_process_turn_ws`
    did not, and the drawer speaks only to the socket. A file-level check would have
    passed while the feature was broken, so this looks inside the websocket handler.
    """
    mounts, modules = _mounts(), _router_modules()

    offenders: list[str] = []
    for prefix in AGENT_CHAT_PREFIXES:
        dotted = modules[mounts[prefix]]
        tree = ast.parse(_module_path(dotted).read_text(encoding="utf-8-sig", errors="replace"))

        # The websocket entry point, and anything it calls in the same module: the turn
        # is often handled in a helper (`_process_turn_ws`), which is good structure and
        # must not hide the call from this check.
        functions = {
            n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        ws_names = [
            n.name for n in functions.values()
            if any(getattr(d.func, "attr", None) == "websocket"
                   for d in n.decorator_list if isinstance(d, ast.Call))
        ]
        reachable, seen = list(ws_names), set()
        reads = False
        while reachable:
            name = reachable.pop()
            if name in seen:
                continue
            seen.add(name)
            fn = functions.get(name)
            if fn is None:
                continue
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                called = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if called == READS_ATTACHMENTS:
                    reads = True
                elif called in functions:
                    reachable.append(called)
        if not reads:
            offenders.append(f"{prefix} -> {dotted}")

    assert not offenders, (
        "The socket handler — the one the chat drawer uses — never reads attachments "
        "in:\n  " + "\n  ".join(offenders)
    )
