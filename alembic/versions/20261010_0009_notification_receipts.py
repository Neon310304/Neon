import sqlalchemy as sa

from alembic import op

revision = "20261010_0009"
down_revision = "20261010_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_receipts",
        sa.Column("subject_key", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["request_events.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("notification_receipts")
