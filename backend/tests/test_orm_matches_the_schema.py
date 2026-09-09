"""The models and the database agree about what may be NULL.

WHY NULLABILITY SPECIFICALLY, and not full schema drift. A missing table fails loudly
the first time anything touches it. A nullability disagreement never fails: SQLAlchemy
does not validate on read, so a column the database allows to be NULL, declared
`Mapped[str]`, hands back `None` from a field whose type says it cannot be. Every
reader downstream is written against the promise rather than the data.

THE ONE THIS CAUGHT. `model_providers.secret_ref` was declared `nullable=False` while
migration 0017 had deliberately made it nullable for keyless onboarding — a connection
registered with no key so its models can be granted centrally, and the state a provider
returns to when its key is cleared. The declaration had been wrong for the entire life
of that feature. Tightening the column to match the model instead would have deleted
every keyless provider: 0017's own downgrade has to `DELETE FROM model_providers WHERE
secret_ref IS NULL` for exactly that reason.

The direction matters, so this test does not guess: it reports the disagreement and
leaves which side is wrong to whoever reads it.
"""
from __future__ import annotations

import importlib
import pathlib
import pkgutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.integration


def _metadata():
    """Every model module imported, or the comparison silently covers a subset."""
    import shared.models as models_pkg

    for mod in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"shared.models.{mod.name}")
    from shared.models.orm import Base

    return Base.metadata


@pytest.mark.asyncio
async def test_no_column_promises_more_than_the_database_does():
    from sqlalchemy import create_engine

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from config.env import POSTGRES_CONN_STRING

    dsn = POSTGRES_CONN_STRING.replace("postgresql+asyncpg", "postgresql+psycopg2")
    metadata = _metadata()

    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), metadata)
    finally:
        engine.dispose()

    # compare_metadata returns column-level changes as a LIST of tuples; everything
    # else (add_table, remove_index, ...) comes back as a bare tuple. Only the
    # nullability entries are this test's business — the schema carries indexes and
    # tables the models do not declare (LangGraph's checkpoint tables, for one), and
    # failing on those would make the guard noise nobody reads.
    mismatches = [
        entry
        for change in diff
        if isinstance(change, list)
        for entry in change
        if isinstance(entry, tuple) and entry and entry[0] == "modify_nullable"
    ]

    assert not mismatches, (
        "The ORM and the database disagree about NULL:\n  "
        + "\n  ".join(
            f"{m[2]}.{m[3]}: database nullable={m[5]}, model nullable={m[6]}"
            for m in mismatches
        )
        + "\n\nDecide WHICH is wrong. A migration that tightens a column runs against "
          "existing rows; changing the model is free. Read the migration that made it "
          "nullable before assuming the model is right."
    )


def test_the_comparison_actually_sees_the_models():
    """A metadata object that ended up empty would make the test above pass while
    comparing nothing at all."""
    tables = _metadata().tables

    # A floor, not a count: 37 today, and a number that tracked it exactly would fail
    # on every new model for no reason. Named tables alongside it, so a metadata that
    # loaded SOME module but not the ones this test is about still fails.
    assert len(tables) > 30, f"only {len(tables)} tables in the metadata"
    for name in ("model_providers", "projects", "runs", "artifacts"):
        assert name in tables, f"{name} missing from the compared metadata"
