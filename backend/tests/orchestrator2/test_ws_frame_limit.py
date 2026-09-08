"""An inbound frame is bounded before it is parsed.

`_remember` bounds what is RETAINED — twenty 1 MB messages no longer means 20 MB held
and re-sent to the routing model on every turn. It does nothing about what ARRIVES: a
single 20 MB frame was still received and json-parsed first, which is the half that was
missing.

uvicorn's own `ws_max_size` is the real defence, at the protocol layer, and is set for
the `python process_api.py` path. The CLI path (`uv run uvicorn ...`) takes a flag this
code cannot enforce, so the handler refuses oversized frames itself. That is the only
layer we fully control, and it is why both exist.
"""
import inspect

from agents_orchestrator.orchestrator2 import ws


def test_the_limit_is_declared_and_sane():
    assert isinstance(ws.MAX_INBOUND_FRAME_BYTES, int)
    # Large enough for a genuinely long pasted document, small enough to bound memory.
    assert 64_000 <= ws.MAX_INBOUND_FRAME_BYTES <= 4_000_000


def test_an_oversized_frame_is_refused():
    huge = "x" * (ws.MAX_INBOUND_FRAME_BYTES + 1)
    assert ws._frame_too_large(huge) is True


def test_a_normal_frame_passes():
    assert ws._frame_too_large('{"type":"user_message","text":"hi","run_id":"r"}') is False


def test_a_frame_exactly_at_the_limit_is_allowed():
    """Off-by-one matters here: the limit is a maximum, not a threshold to exceed."""
    assert ws._frame_too_large("x" * ws.MAX_INBOUND_FRAME_BYTES) is False


def test_the_limit_is_measured_in_bytes_not_characters():
    """A message of astral-plane characters is four bytes each. Measuring length would
    let a frame four times the limit through — the exact thing being bounded."""
    emoji = "\U0001F600"  # 4 bytes in UTF-8, len() == 1
    just_over = emoji * (ws.MAX_INBOUND_FRAME_BYTES // 4 + 1)
    assert len(just_over) < ws.MAX_INBOUND_FRAME_BYTES, "the char count must be UNDER"
    assert ws._frame_too_large(just_over) is True, "but the byte count is over"


def test_the_check_runs_before_the_frame_is_parsed():
    """Refusing after `json.loads` would already have paid the memory cost the limit
    exists to avoid. Read off the source order rather than trusted."""
    src = inspect.getsource(ws.orchestrator2_ws)
    guard = src.index("_frame_too_large")
    parse = src.index("json.loads")
    assert guard < parse, "the size guard must come before json.loads"


def test_uvicorn_is_configured_with_a_ws_max_size():
    """Covers the `python process_api.py` path. The CLI path needs --ws-max-size, which
    docs/local-setup.md records; neither covers every deployment, which is why the
    handler check exists as well."""
    import process_api

    assert "ws_max_size" in inspect.getsource(process_api)
