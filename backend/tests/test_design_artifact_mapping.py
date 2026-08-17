from shared.models.design import parse_artifact_sections, DesignArtifacts
from shared.models.artifacts import DesignArtifact


_DOC = """\
## HIGH-LEVEL DESIGN
Overview here.

## LOW-LEVEL DESIGN
Details here.

## C4 ARCHITECTURE DIAGRAMS
Diagrams here.

## API CONTRACT
openapi spec here.

## DATABASE SCHEMA
create table here.

## ARCHITECTURE DECISION RECORDS
ADR-001 here.

## TECHNOLOGY STACK
stack table here.

## SECURITY DESIGN CHECKLIST
A01 Broken Access Control: mitigated by RBAC.
A02 Cryptographic Failures: TLS 1.3 + at-rest encryption.
"""


def test_parse_captures_security_checklist():
    arts = parse_artifact_sections(_DOC)
    assert arts.hld == "Overview here."
    assert arts.tech_stack == "stack table here."
    assert arts.security_checklist is not None
    assert "Broken Access Control" in arts.security_checklist


def test_design_artifacts_has_security_field_default_none():
    arts = DesignArtifacts()
    assert arts.security_checklist is None


def test_design_artifact_model_has_security_field():
    art = DesignArtifact(hld="x", security_checklist="A01: ok")
    dumped = art.model_dump()
    assert dumped["security_checklist"] == "A01: ok"


from config.context_broker import _fmt_design


def _build_design_artifact_from_doc(doc: str) -> DesignArtifact:
    """Mirror the pipeline cold-path mapping under test (Task 5)."""
    parsed = parse_artifact_sections(doc)
    return DesignArtifact(
        hld=parsed.hld,
        lld=parsed.lld,
        api_contracts=parsed.api_contract,
        database_schema=parsed.database_schema,
        c4_diagram_url=parsed.c4_diagrams,
        security_checklist=parsed.security_checklist,
        version=1,
    )


def test_cold_path_mapping_populates_all_sections():
    art = _build_design_artifact_from_doc(_DOC)
    assert art.hld == "Overview here."
    assert art.lld == "Details here."
    assert art.api_contracts == "openapi spec here."
    assert art.database_schema == "create table here."
    assert art.c4_diagram_url == "Diagrams here."
    assert art.security_checklist is not None
    # Regression: hld must NOT be the whole document.
    assert "## LOW-LEVEL DESIGN" not in (art.hld or "")


def test_fmt_design_renders_all_keys():
    art = _build_design_artifact_from_doc(_DOC)
    out = _fmt_design(art.model_dump())
    assert "HLD" in out
    assert "LLD" in out
    assert "API_CONTRACTS" in out
    assert "DATABASE_SCHEMA" in out
    assert "C4_DIAGRAM_URL" in out
    assert "SECURITY_CHECKLIST" in out
    assert "Diagrams here." in out  # the C4 content actually reaches the dev agent
