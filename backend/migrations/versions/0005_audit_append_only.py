"""Restore append-only enforcement on audit_events.

The original migration 0008 revoked UPDATE and DELETE on audit_events from the app
role, which is what makes the audit trail evidence rather than a log. The 0001 squash
recreated the table but did not carry the REVOKE forward.

That went unnoticed because the app role held no privileges on the table at all, so
nothing could be updated for want of any grant — append-only held by accident. The
moment an operator granted the app role its normal DML (which it needs, to INSERT),
UPDATE and DELETE came with it and the property silently disappeared.

Enforced here as a REVOKE rather than a trigger so it is a privilege of the role, not
a rule the application could be talked out of: even a SQL-injection foothold running
as sdlc_app cannot rewrite history it is not granted to rewrite.

SELECT and INSERT are granted explicitly first: the app must be able to read the trail
and append to it, and this migration should leave the table correct regardless of what
the role was granted before.

Revision ID: 0005_audit_append_only
Revises: 0004_custom_role_scope
"""
from alembic import op

revision = "0005_audit_append_only"
down_revision = "0004_custom_role_scope"
branch_labels = None
depends_on = None

# Tables whose rows must never be modified after insert. agent_call_logs is the other
# immutable-after-insert table (per its ORM docstring) but is deliberately NOT included:
# it is telemetry, not evidence, and it is not the subject of the original REVOKE.
_APPEND_ONLY_TABLES = ("audit_events",)


def upgrade() -> None:
    for table in _APPEND_ONLY_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                    GRANT SELECT, INSERT ON {table} TO sdlc_app;
                    REVOKE UPDATE, DELETE ON {table} FROM sdlc_app;
                END IF;
            END
            $$;
            """
        )
        # Future-proofing: ALTER DEFAULT PRIVILEGES set up by an operator grants full
        # DML on new tables, but these tables already exist, so the revoke above is
        # what governs them from here.


def downgrade() -> None:
    for table in _APPEND_ONLY_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                    GRANT UPDATE, DELETE ON {table} TO sdlc_app;
                END IF;
            END
            $$;
            """
        )
