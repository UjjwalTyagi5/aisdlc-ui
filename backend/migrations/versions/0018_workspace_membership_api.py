"""0018 workspace membership API — no DDL, feature activation marker.

All required tables (user_workspace_roles, users, roles, workspaces) were
created in prior migrations (0007, 0001).  This revision activates the
workspace-scoped membership API:

  GET    /workspaces                          — now filters by membership for
                                               non-admin users
  POST   /workspaces                          — auto-adds creator as admin member
  GET    /workspaces/{id}/members             — list workspace members
  POST   /workspaces/{id}/members             — add member (userId or email + role)
  PATCH  /workspaces/{id}/members/{user_id}   — change member's role
  DELETE /workspaces/{id}/members/{user_id}   — remove member

No schema changes — this is a pure application-layer feature.  The migration is
a marker so alembic history stays in sync with the changelog.

Revision ID: 0018
Revises: 0017
"""

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # No DDL — see module docstring.


def downgrade() -> None:
    pass
