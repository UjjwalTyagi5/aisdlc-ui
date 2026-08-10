# platform/backend/agents_orchestrator/design_architecture_agent/tools/schema_validation_tool.py
"""Database-schema (DDL) validation tool for the Design Agent (reference §4.2 Step 6).

Reference Step 6 names SchemaSpy. SchemaSpy needs a live DB + JDBC driver, so it is
rarely available in dev/CI. This tool reports whether SchemaSpy is present, but ALWAYS
runs a deterministic static validation of the generated CREATE TABLE DDL (using sqlparse
when installed, else a pure-regex fallback) so the design correction loop has structured
findings even with no binary. Degrades gracefully; never raises.
"""
from __future__ import annotations

import json
import logging
import re
import shutil

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SCHEMASPY_BIN = shutil.which("schemaspy")

# CREATE TABLE <name> ( ... )  — name may be quoted/qualified; body is the paren group.
_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?(?P<name>[\w.]+)[`\"\]]?\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_PK = re.compile(r"\bprimary\s+key\b", re.IGNORECASE)
_FK = re.compile(r"\b(references|foreign\s+key)\b", re.IGNORECASE)


def _parse_tables(ddl: str) -> list[dict]:
    """Return [{name, has_pk, has_fk}] for each CREATE TABLE block.

    Uses sqlparse to strip comments when available; the regex extraction itself
    works with or without sqlparse.
    """
    cleaned = ddl
    try:
        import sqlparse  # noqa: PLC0415
        cleaned = sqlparse.format(ddl, strip_comments=True)
    except Exception:
        pass

    tables: list[dict] = []
    for m in _CREATE_TABLE.finditer(cleaned):
        body = m.group("body")
        tables.append({
            "name": m.group("name"),
            "has_pk": bool(_PK.search(body)),
            "has_fk": bool(_FK.search(body)),
        })
    return tables


@tool
async def validate_database_schema(ddl: str) -> str:
    """Validate generated database DDL (CREATE TABLE statements) for the design.

    Always runs a static structural check: flags tables with no PRIMARY KEY and DDL
    with no tables. Detects whether SchemaSpy is installed (reported via
    `schemaspy_available`) but does NOT execute it. Feed the returned `issues` back
    into the design to correct the schema, then re-validate (Step 6 loop).

    Args:
        ddl: The DATABASE SCHEMA section DDL (one or more CREATE TABLE statements).

    Returns:
        JSON string of validation findings.
    """
    if not ddl or not ddl.strip():
        return json.dumps({
            "status": "empty", "engine": "static", "table_count": 0,
            "tables": [], "issues": [], "schemaspy_available": _SCHEMASPY_BIN is not None,
        })

    tables = _parse_tables(ddl)
    issues: list[dict] = []
    if not tables:
        issues.append({"table": "", "issue": "no_tables_found",
                       "detail": "No CREATE TABLE statement was detected in the DDL."})
    for t in tables:
        if not t["has_pk"]:
            issues.append({"table": t["name"], "issue": "missing_primary_key",
                           "detail": "Table has no PRIMARY KEY — add a surrogate or natural key."})

    return json.dumps({
        "status": "ok",
        "engine": "static",
        "table_count": len(tables),
        "tables": [{"name": t["name"], "has_pk": t["has_pk"], "has_fk": t["has_fk"]} for t in tables],
        "issues": issues,
        "schemaspy_available": _SCHEMASPY_BIN is not None,
    })
