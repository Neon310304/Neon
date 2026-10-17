import sqlalchemy as sa

from alembic import op

revision = "20261009_0005"
down_revision = "20261003_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("residents", sa.Column("building", sa.String(80), nullable=False, server_default=""))
    op.create_table(
        "local_credentials",
        sa.Column("username", sa.String(80), primary_key=True),
        sa.Column("auth_subject", sa.String(160), sa.ForeignKey("users.auth_subject", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("resident_id", sa.String(32), sa.ForeignKey("residents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("salt", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("creation_key", sa.String(100), nullable=False),
        sa.Column("creation_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("creation_key", name="uq_local_credentials_creation_key"),
    )
    op.create_table(
        "regulation_acknowledgements",
        sa.Column("resident_id", sa.String(32), sa.ForeignKey("residents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.String(120), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("version", sa.String(160), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("regulation_acknowledgements")
    op.drop_table("local_credentials")
    op.drop_column("residents", "building")
