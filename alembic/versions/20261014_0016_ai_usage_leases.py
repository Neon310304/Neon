import sqlalchemy as sa

from alembic import op

revision = "20261014_0016"
down_revision = "20261014_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ai_usage_buckets",
                    sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("used", sa.Integer(), nullable=False))
    op.create_table("ai_request_leases",
                    sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("operation", sa.String(12), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_ai_request_leases_operation", "ai_request_leases", ["operation"])
    op.create_index("ix_ai_request_leases_expires_at", "ai_request_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_table("ai_request_leases")
    op.drop_table("ai_usage_buckets")
