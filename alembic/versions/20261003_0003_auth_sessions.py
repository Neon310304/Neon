"""Add local Supabase session revocation records.

Revision ID: 20261003_0003
Revises: 20261003_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_0003"
down_revision: str | None = "20261003_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revoked_auth_sessions",
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("auth_subject", sa.String(length=160), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("session_id", name="pk_revoked_auth_sessions"),
    )
    op.create_index(
        "ix_revoked_auth_sessions_revoked_at",
        "revoked_auth_sessions",
        ["revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_revoked_auth_sessions_revoked_at", table_name="revoked_auth_sessions")
    op.drop_table("revoked_auth_sessions")
