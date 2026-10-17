"""Add provider-neutral users and scoped memberships.

Revision ID: 20261003_0002
Revises: 20261001_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_0002"
down_revision: str | None = "20261001_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("auth_subject", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'suspended', 'disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("auth_subject", name="uq_users_auth_subject"),
    )
    op.create_table(
        "residence_memberships",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("resident_id", sa.String(length=32), nullable=True),
        sa.Column("site_id", sa.String(length=60), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('resident', 'manager')", name="ck_memberships_role"),
        sa.CheckConstraint("status IN ('active', 'suspended', 'expired')", name="ck_memberships_status"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"], ["residents.id"], name="fk_memberships_resident_id_residents", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_residence_memberships"),
        sa.UniqueConstraint(
            "user_id", "resident_id", "site_id", "role", name="uq_memberships_identity_scope"
        ),
    )
    op.create_index(
        "ix_memberships_subject_scope",
        "residence_memberships",
        ["user_id", "status", "site_id"],
        unique=False,
    )
    op.create_index(
        "ix_memberships_resident_scope",
        "residence_memberships",
        ["resident_id", "status"],
        unique=False,
    )
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("actor_subject", sa.String(length=160), nullable=True),
        sa.Column("user_id", sa.String(length=40), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("site_id", sa.String(length=60), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_security_audit_events"),
    )
    op.create_index("ix_security_audit_events_created_at", "security_audit_events", ["created_at"], unique=False)
    op.create_index(
        "ix_security_audit_events_resource",
        "security_audit_events",
        ["resource_type", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_security_audit_events_resource", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_created_at", table_name="security_audit_events")
    op.drop_table("security_audit_events")
    op.drop_index("ix_memberships_resident_scope", table_name="residence_memberships")
    op.drop_index("ix_memberships_subject_scope", table_name="residence_memberships")
    op.drop_table("residence_memberships")
    op.drop_table("users")
