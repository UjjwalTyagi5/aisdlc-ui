"""Immutable, numbered versions of a stage's artifact payload.

WHY THIS TABLE EXISTS. Agents hand work to each other through
`runs.{stage}_artifacts` — a JSONB column that `patch_session_artifacts` writes IN
PLACE. There is no history. Approving that column would be theatre: the approval
record survives, the thing it approved does not, and the next run silently replaces
it. Every later phase of the artifact-publication work (a publication gate, consumers
that read only approved work, an audit answer to "what did this run build on") rests
on there being something that cannot change once signed.

`runs.{stage}_artifacts` STAYS as the agent's working draft. This table is not a
replacement for it; publishing COPIES the draft into a frozen version. A new run
creates version N+1 and never edits N.

IMMUTABILITY IS ENFORCED BY A TRIGGER, NOT BY CONVENTION. A service-layer rule that
"we never update payload" is one careless `UPDATE` away from being false, and the
failure would be invisible — the hash would still match its own recomputation, and a
signed version would quietly mean something new. `artifact_versions_freeze` rejects
any change to the identity or the content of a row; only the lifecycle columns
(status, published_by, published_at, rejection_reason) may move.

RLS KEYS OFF `app.current_tenant_id`, NOT `app.tenant_id`. Every other tenant-scoped
table in this schema uses that name, and the app role is not a Postgres superuser, so
the wrong GUC name does not error — it reads as a permanently empty table.

NO CHECK CONSTRAINT ON `stage`. The valid stages derive from `AGENT_REGISTRY` via
`progression.STAGE_ORDER`; pinning them here would mean a migration every time an
agent is added, and a DB/code disagreement is exactly the drift this whole workstream
is removing. The service validates against STAGE_ORDER instead.

Revision ID: 0045_artifact_versions

(Under 32 characters: `alembic_version.version_num` is varchar(32), and a longer id
fails at the very END — every statement succeeds, then the version stamp raises
StringDataRightTruncation and the whole transaction rolls back. See 0037.)

Revises: 0044_scrum_master_decide
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_artifact_versions"
down_revision = "0044_scrum_master_decide"
branch_labels = None
depends_on = None

_TABLE = "artifact_versions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The producing stage, e.g. 'requirements'. Backend stage names
        # (progression.STAGE_ORDER), never the UI phase names — `code_review`, not
        # `review`. The two diverging is what made the owner map wrong for months.
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),

        # 1, 2, 3 … per (project, stage). Allocated under a unique constraint, so two
        # concurrent runs cannot both take N — one fails and retries.
        sa.Column("version", sa.Integer, nullable=False),

        # FROZEN AT CREATION. See the trigger below.
        sa.Column("payload", postgresql.JSONB, nullable=False),
        # sha256 of the canonical payload, so "did this actually change?" is
        # answerable without diffing JSONB, and an audit row can name what was signed.
        sa.Column("content_hash", sa.String(64), nullable=False),
        # Blob artifact ids (`artifacts.id`) this version signs off, so a design
        # document and its diagram publish as one unit. No FK: it is a list, and the
        # rows it points at keep their own independent approval_status.
        sa.Column("covers", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),

        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        # The person whose run produced it. Compared against published_by to refuse
        # self-publication, which is why it is NOT NULL.
        sa.Column("produced_by", sa.String(255), nullable=False),
        sa.Column("published_by", sa.String(255)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        # A version outlives the run that made it — deleting a run must not destroy
        # the evidence of what was approved.
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),

        # One version number per (project, stage). This is also the concurrency
        # control for allocating N+1.
        sa.UniqueConstraint("project_id", "stage", "version",
                            name="uq_artifact_versions_project_stage_version"),

        sa.CheckConstraint(
            "status IN ('draft','published','rejected','superseded')",
            name="ck_artifact_versions_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_artifact_versions_version_positive"),
        # A publication names its publisher and when. A version published by nobody is
        # not published — the same rule the deployments table enforces for approvals.
        sa.CheckConstraint(
            "status <> 'published' OR (published_by IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="ck_artifact_versions_published_by_someone",
        ),
        # SELF-PUBLICATION IS REFUSED IN THE SCHEMA, not only in the service. The
        # producing agent runs AS A PERSON, so the comparison is always available.
        sa.CheckConstraint(
            "published_by IS NULL OR published_by <> produced_by",
            name="ck_artifact_versions_no_self_publication",
        ),
        # A rejection says why. An unexplained rejection is indistinguishable from a
        # mistake to the person who has to act on it.
        sa.CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_artifact_versions_rejection_has_reason",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(covers) = 'array'", name="ck_artifact_versions_covers_array",
        ),
    )

    op.create_index("ix_artifact_versions_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_artifact_versions_run_id", _TABLE, ["run_id"])
    # THE READ EVERY CONSUMER MAKES: the latest published version of one stage.
    op.create_index(
        "ix_artifact_versions_published", _TABLE,
        ["project_id", "stage", sa.text("version DESC")],
        postgresql_where=sa.text("status = 'published'"),
    )
    # The stage page's list, and the next-version allocation.
    op.create_index(
        "ix_artifact_versions_project_stage", _TABLE,
        ["project_id", "stage", sa.text("version DESC")],
    )

    # ── immutability ─────────────────────────────────────────────────────────
    # Identity and content are write-once. Lifecycle columns are deliberately absent
    # from this list; moving draft -> published -> superseded is the point of the row.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION artifact_versions_freeze()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
               OR NEW.project_id IS DISTINCT FROM OLD.project_id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.stage IS DISTINCT FROM OLD.stage
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.covers IS DISTINCT FROM OLD.covers
               OR NEW.produced_by IS DISTINCT FROM OLD.produced_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION
                    'artifact_versions row % is frozen: payload, hash, identity and '
                    'covers cannot be modified after creation (attempted on stage %, '
                    'version %)', OLD.id, OLD.stage, OLD.version
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_artifact_versions_freeze "
        f"BEFORE UPDATE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION artifact_versions_freeze()"
    )

    # ── tenant isolation ─────────────────────────────────────────────────────
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {_TABLE} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    # FORCE, so the table owner is subject to the policy too.
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_artifact_versions_freeze ON {_TABLE}")
    op.execute("DROP FUNCTION IF EXISTS artifact_versions_freeze()")
    op.drop_index("ix_artifact_versions_project_stage", table_name=_TABLE)
    op.drop_index("ix_artifact_versions_published", table_name=_TABLE)
    op.drop_index("ix_artifact_versions_run_id", table_name=_TABLE)
    op.drop_index("ix_artifact_versions_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
