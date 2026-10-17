import sqlalchemy as sa

from alembic import op

revision = "20261013_0011"
down_revision = "20261011_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("local_credentials", sa.Column("recovery_email", sa.String(254), nullable=True))
    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(80), sa.ForeignKey("local_credentials.username", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "password_recovery_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("apartment", sa.String(40), nullable=False),
        sa.Column("building", sa.String(80), nullable=False),
        sa.Column("contact", sa.String(160), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_password_recovery_requests_username", "password_recovery_requests", ["username"])


def downgrade() -> None:
    op.drop_table("password_recovery_requests")
    op.drop_table("auth_challenges")
    op.drop_column("local_credentials", "recovery_email")
