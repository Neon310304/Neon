import sqlalchemy as sa

from alembic import op

revision = "20261010_0008"
down_revision = "20261010_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle_documents",
        sa.Column("request_id", sa.String(40), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["management_requests.id"], ondelete="CASCADE"),
        sa.CheckConstraint("octet_length(content) <= 5242880", name="ck_vehicle_document_size"),
    )


def downgrade() -> None:
    op.drop_table("vehicle_documents")
