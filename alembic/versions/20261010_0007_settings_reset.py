import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20261010_0007"
down_revision = "20261009_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_settings",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "login_reset_baselines",
        sa.Column("resident_id", sa.String(32), primary_key=True),
        sa.Column("site_id", sa.String(60), nullable=False),
        sa.Column("profile", JSONB(), nullable=False),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("resident_id = 'R001'", name="ck_login_reset_synthetic_resident"),
    )
    op.create_table(
        "login_reset_attempts",
        sa.Column("bucket", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("login_reset_attempts")
    op.drop_table("login_reset_baselines")
    op.drop_table("application_settings")
