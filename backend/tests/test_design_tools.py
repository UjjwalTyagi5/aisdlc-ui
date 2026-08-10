import json

import pytest

from shared.tools.spectral_tool import run_spectral_lint


@pytest.mark.asyncio
async def test_spectral_missing_path():
    out = await run_spectral_lint.ainvoke({"spec_path": "/no/such/spec.yaml"})
    data = json.loads(out)
    # Either CLI is absent (unavailable) or the path check fires (error) — never raise.
    assert data["status"] in ("unavailable", "error")
    assert data["findings"] == []


def test_requirements_spectral_reexport_is_same_object():
    from agents_orchestrator.requirements_agent.tools.spectral_tool import (
        run_spectral_lint as req_tool,
    )
    assert req_tool is run_spectral_lint


from agents_orchestrator.design_architecture_agent.tools.schema_validation_tool import (
    validate_database_schema,
)


@pytest.mark.asyncio
async def test_schema_validation_flags_missing_pk():
    ddl = "CREATE TABLE orders (id INTEGER, customer_id INTEGER);"
    out = await validate_database_schema.ainvoke({"ddl": ddl})
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["table_count"] == 1
    issues = {i["issue"] for i in data["issues"]}
    assert "missing_primary_key" in issues


@pytest.mark.asyncio
async def test_schema_validation_clean_ddl_has_no_pk_issue():
    ddl = (
        "CREATE TABLE customer (\n"
        "  id SERIAL PRIMARY KEY,\n"
        "  name VARCHAR(255) NOT NULL\n"
        ");"
    )
    out = await validate_database_schema.ainvoke({"ddl": ddl})
    data = json.loads(out)
    assert data["status"] == "ok"
    issues = {i["issue"] for i in data["issues"]}
    assert "missing_primary_key" not in issues


@pytest.mark.asyncio
async def test_schema_validation_empty_ddl():
    out = await validate_database_schema.ainvoke({"ddl": "   "})
    data = json.loads(out)
    assert data["status"] == "empty"
    assert data["tables"] == []


@pytest.mark.asyncio
async def test_schema_validation_no_create_table():
    out = await validate_database_schema.ainvoke({"ddl": "SELECT 1;"})
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["table_count"] == 0
    issues = {i["issue"] for i in data["issues"]}
    assert "no_tables_found" in issues


from agents_orchestrator.design_architecture_agent.tools.existing_system_tool import (
    analyze_existing_system,
)


@pytest.mark.asyncio
async def test_existing_system_missing_path():
    out = await analyze_existing_system.ainvoke({"repo_path": "/no/such/repo"})
    data = json.loads(out)
    assert data["status"] == "unavailable"
    assert data["frameworks"] == []


@pytest.mark.asyncio
async def test_existing_system_detects_frameworks(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110\nsqlalchemy==2.0\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "models.py").write_text("from sqlalchemy import Column\n")
    out = await analyze_existing_system.ainvoke({"repo_path": str(tmp_path)})
    data = json.loads(out)
    assert data["status"] == "ok"
    assert "fastapi" in data["frameworks"]
    assert "python" in data["languages"]
    assert any("models.py" in f for f in data["db_files"])
