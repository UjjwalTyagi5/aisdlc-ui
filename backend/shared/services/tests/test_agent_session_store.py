import pytest

from shared.services import agent_session_store as store

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_patch_then_fetch_roundtrips_design_artifacts():
    sid = "test-sess-design-1"
    await store.patch_session_artifacts(sid, {"design_artifacts": {"hld": "x"}})
    got = await store.fetch_session_artifacts(sid)
    assert got is not None
    assert got["design_artifacts"] == {"hld": "x"}
    assert got["session_id"] == sid


async def test_patch_is_partial_does_not_clobber_other_fields():
    sid = "test-sess-partial-1"

    # Seed the row with BOTH fields present so we prove a genuine pre-existing value
    # survives a subsequent patch that only touches the other field.
    await store.patch_session_artifacts(sid, {
        "requirements_payload": {"stories": [1]},
        "design_artifacts": {"hld": "original"},
    })

    # Patch ONLY requirements_payload to a new value — design_artifacts must survive.
    await store.patch_session_artifacts(sid, {"requirements_payload": {"stories": [2]}})

    got = await store.fetch_session_artifacts(sid)
    assert got["requirements_payload"] == {"stories": [2]}, (
        "patched field should reflect new value"
    )
    assert got["design_artifacts"] == {"hld": "original"}, (
        "untouched field must not be clobbered by a partial patch"
    )


async def test_fetch_unknown_session_returns_none():
    assert await store.fetch_session_artifacts("does-not-exist-zzz") is None


async def test_coerce_json_parses_stringified_payload():
    sid = "test-sess-coerce-1"
    await store.patch_session_artifacts(sid, {"requirements_payload": '{"stories": []}'})
    got = await store.fetch_session_artifacts(sid)
    assert got["requirements_payload"] == {"stories": []}


async def test_only_patchable_fields_are_written():
    sid = "test-sess-allowlist-1"
    await store.patch_session_artifacts(sid, {"design_artifacts": {"a": 1}, "not_a_column": "nope"})
    got = await store.fetch_session_artifacts(sid)
    assert got["design_artifacts"] == {"a": 1}
    assert "not_a_column" not in got


async def test_orchestrator_state_set_get_clear():
    cs = "test-chat-1"
    await store.set_orchestrator_state(cs, current_active_agent="design")
    st = await store.get_orchestrator_state(cs)
    assert st["current_active_agent"] == "design"
    await store.clear_orchestrator_state(cs)
    assert await store.get_orchestrator_state(cs) is None
