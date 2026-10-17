"""Create the MoveInMate MVP PostgreSQL schema.

Revision ID: 20261001_0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261001_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "residents",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("apartment", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=60), nullable=False),
        sa.Column("site_name", sa.String(length=120), nullable=False),
        sa.Column("resident_type", sa.String(length=30), nullable=False),
        sa.Column("family_members", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("vehicle", sa.String(length=20), server_default=sa.text("'none'"), nullable=False),
        sa.Column("needs_internet", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("profile_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "family_members BETWEEN 1 AND 20",
            name="ck_residents_family_members_range",
        ),
        sa.CheckConstraint(
            "vehicle IN ('none', 'motorbike', 'car')",
            name="ck_residents_vehicle",
        ),
        sa.CheckConstraint("profile_version >= 1", name="ck_residents_profile_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_residents"),
    )
    op.create_index("ix_residents_site_id", "residents", ["site_id"], unique=False)

    op.create_table(
        "checklist_tasks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("resident_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["residents.id"],
            name="fk_checklist_tasks_resident_id_residents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_checklist_tasks"),
        sa.UniqueConstraint("resident_id", "code", name="uq_checklist_tasks_resident_code"),
    )

    op.create_table(
        "management_requests",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("resident_id", sa.String(length=32), nullable=False),
        sa.Column("request_type", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'submitted'"), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('submitted', 'in_review', 'needs_info', 'approved', "
            "'rejected', 'completed', 'cancelled_by_resident')",
            name="ck_management_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["residents.id"],
            name="fk_management_requests_resident_id_residents",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_management_requests"),
        sa.UniqueConstraint("id", "resident_id", name="uq_management_requests_id_resident"),
        sa.UniqueConstraint("idempotency_key", name="uq_management_requests_idempotency_key"),
    )
    op.create_index(
        "ix_management_requests_resident_created",
        "management_requests",
        ["resident_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_management_requests_status_updated",
        "management_requests",
        ["status", sa.text("updated_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_management_requests_resident_type_status",
        "management_requests",
        ["resident_id", "request_type", "status"],
        unique=False,
    )
    op.create_index(
        "uq_management_requests_active_business_key",
        "management_requests",
        ["resident_id", "request_type", "business_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('submitted', 'in_review', 'needs_info', 'approved')"),
    )

    op.create_table(
        "request_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["management_requests.id"],
            name="fk_request_events_request_id_management_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_request_events"),
    )
    op.create_index("ix_request_events_request_id_id", "request_events", ["request_id", "id"], unique=False)

    op.create_table(
        "parking_reminders",
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("resident_id", sa.String(length=32), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["management_requests.id"],
            name="fk_parking_reminders_request_id_management_requests",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["residents.id"],
            name="fk_parking_reminders_resident_id_residents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "resident_id"],
            ["management_requests.id", "management_requests.resident_id"],
            name="fk_parking_reminders_request_resident_management_requests",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id", name="pk_parking_reminders"),
    )
    op.create_index("ix_parking_reminders_resident_id", "parking_reminders", ["resident_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_parking_reminders_resident_id", table_name="parking_reminders")
    op.drop_table("parking_reminders")
    op.drop_index("ix_request_events_request_id_id", table_name="request_events")
    op.drop_table("request_events")
    op.drop_index(
        "uq_management_requests_active_business_key",
        table_name="management_requests",
        postgresql_where=sa.text("status IN ('submitted', 'in_review', 'needs_info', 'approved')"),
    )
    op.drop_index("ix_management_requests_resident_type_status", table_name="management_requests")
    op.drop_index("ix_management_requests_status_updated", table_name="management_requests")
    op.drop_index("ix_management_requests_resident_created", table_name="management_requests")
    op.drop_table("management_requests")
    op.drop_table("checklist_tasks")
    op.drop_index("ix_residents_site_id", table_name="residents")
    op.drop_table("residents")
