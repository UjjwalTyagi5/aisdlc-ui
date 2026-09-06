"""The FIELD shapes of the deliverable event, checked against the Zod schema.

`test_every_emitted_event_type_is_in_the_frontend_union` compares event TYPES only.
Both bugs this union has already produced were field-shape mismatches that would have
passed that test: the frame was the right type and was dropped anyway, because
`protocol.ts` validates every inbound frame with `safeParse` and DISCARDS what fails.

A dropped frame produces no error on either side. On the backend `json.dumps`
succeeds; in the browser the frame is simply not there. What the user sees is an
agent that produced nothing — which is the exact failure this whole rebuild exists to
eliminate, so it is worth a test that reads both sides.
"""
import datetime as dt
import re
from pathlib import Path

SCHEMA = (
    Path(__file__).resolve().parents[3]
    / "frontend" / "lib" / "orchestrator" / "deliverables.ts"
)


class _Row:
    id = "d-1"
    run_id = "r-1"
    tenant_id = "t-1"
    project_id = None
    agent_id = "security"
    kind = "markdown"
    title = "Security Report"
    content = "body"
    url = None
    language = None
    source = None
    created_at = dt.datetime(2026, 9, 6, 10, 0, tzinfo=dt.timezone.utc)


def _schema_body() -> str:
    source = SCHEMA.read_text(encoding="utf-8")
    body = source[source.index("export const Deliverable = z.object({"):]
    return body[: body.index("});")]


def _declared_fields() -> set[str]:
    """Field names on the `Deliverable` Zod object.

    Matches ANY declaration, not only `z.`-prefixed ones. The first version required
    `z.` and so reported `agent` (typed `OrchestratorAgentId`) and `kind` (typed
    `ArtifactKind`) as undeclared — a test failing on fields that were declared
    perfectly well. A contract test that cries wolf gets relaxed, and the next real
    mismatch goes through.
    """
    return set(re.findall(r"^\s{2}([a-z_]+):\s*\S", _schema_body(), re.M))


def test_the_schema_file_is_where_this_test_thinks_it_is():
    """A moved file would make every assertion below vacuous by raising early rather
    than passing quietly — but a clear failure beats a confusing one."""
    assert SCHEMA.is_file(), f"no schema at {SCHEMA}"


def test_every_field_the_backend_emits_is_declared_in_the_zod_schema():
    from agents_orchestrator.orchestrator2.deliverables import _to_wire

    emitted = set(_to_wire(_Row()))
    missing = emitted - _declared_fields()
    assert not missing, (
        f"the backend emits {sorted(missing)}, which the Zod schema does not declare "
        "— such a frame is validated, rejected and SILENTLY DROPPED"
    )


def test_every_required_schema_field_is_actually_emitted():
    """The other direction. A field the schema requires and the backend never sends
    fails validation just as completely, and just as invisibly."""
    from agents_orchestrator.orchestrator2.deliverables import _to_wire

    required = {
        name for name, decl in re.findall(r"^\s{2}([a-z_]+):\s*(\S.*)$", _schema_body(), re.M)
        if "nullish" not in decl and "optional" not in decl and "default" not in decl
    }
    emitted = set(_to_wire(_Row()))
    assert required <= emitted, (
        f"the schema requires {sorted(required - emitted)}, which the backend never sends"
    )


def test_the_agent_field_carries_the_id_not_the_column_name():
    """`agent_id` on the column, `agent` on the wire. The schema types `agent` as an
    enum of the nine, so emitting the column name instead would fail validation and
    drop the frame — the same shape as the ErrorEvent.agent bug."""
    from agents_orchestrator.orchestrator2.deliverables import _to_wire

    wire = _to_wire(_Row())
    assert wire["agent"] == "security"
    assert "agent_id" not in wire


def test_every_agent_id_the_backend_can_emit_is_in_the_frontend_enum():
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    source = (
        Path(__file__).resolve().parents[3]
        / "frontend" / "lib" / "orchestrator" / "agents.ts"
    ).read_text(encoding="utf-8")
    block = source[source.index("ORCHESTRATOR_AGENT_IDS = ["):]
    block = block[: block.index("]")]
    declared = set(re.findall(r'"([a-z_]+)"', block))
    assert set(AGENT_IDS) == declared, (
        f"backend-only {sorted(set(AGENT_IDS) - declared)}, "
        f"frontend-only {sorted(declared - set(AGENT_IDS))}"
    )


def test_every_kind_the_backend_can_emit_is_renderable_by_the_frontend():
    """A pointer's kind (`code-tree`, `file-tree`, `link`) has to survive validation
    or the Development code tree — the one per-agent quirk named explicitly in the
    requirement — never reaches the panel."""
    kinds_source = (
        Path(__file__).resolve().parents[3]
        / "frontend" / "lib" / "orchestrator" / "artifacts.ts"
    ).read_text(encoding="utf-8")
    block = kinds_source[kinds_source.index("export const ArtifactKind = z.enum(["):]
    block = block[: block.index("]")]
    renderable = set(re.findall(r'"([a-z-]+)"', block))

    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    emitted = {p["kind"] for p in pointers_for_run(
        {"repo_url": "https://x", "pr_url": "https://y"}, {"testing"}
    )} | {"markdown"}
    assert emitted <= renderable, f"not renderable: {sorted(emitted - renderable)}"


def test_a_pointers_nulls_are_permitted_by_the_schema():
    """Pointers carry `created_at: None`, `url: None`, `source: <agent>` — they are
    references, not documents, so several columns are genuinely null.

    Checked separately from `_to_wire` because a STORED row always has a timestamp:
    tightening `created_at` to a required string passes every other test here and
    still drops every pointer, taking the Development code tree with it. That tree is
    the one per-agent quirk named explicitly in the requirement.
    """
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run

    required = {
        name for name, decl in re.findall(r"^\s{2}([a-z_]+):\s*(\S.*)$", _schema_body(), re.M)
        if "nullish" not in decl and "optional" not in decl and "default" not in decl
    }
    for pointer in pointers_for_run({"repo_url": "https://x", "pr_url": "https://y"},
                                    {"testing"}):
        nulls = {k for k, v in pointer.items() if v is None}
        clash = required & nulls
        assert not clash, (
            f"pointer {pointer['id']} sends null for {sorted(clash)}, which the schema "
            "requires — the frame fails validation and is dropped, so the pointer "
            "never renders"
        )
