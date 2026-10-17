import sqlalchemy as sa

from alembic import op

revision = "20261014_0014"
down_revision = "20261014_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credential_handovers",
        sa.Column("username", sa.String(80), primary_key=True),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["local_credentials.username"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["request_id"], ["management_requests.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("request_id", name="uq_credential_handovers_request_id"),
    )
    op.create_table(
        "request_visibility",
        sa.Column("request_id", sa.String(40), primary_key=True),
        sa.Column("subject_key", sa.String(64), primary_key=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["management_requests.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_request_visibility_subject", "request_visibility", ["subject_key"])


def downgrade() -> None:
    op.drop_table("request_visibility")
    op.drop_table("credential_handovers")
