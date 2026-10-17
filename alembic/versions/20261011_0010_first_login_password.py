import sqlalchemy as sa

from alembic import op

revision = "20261011_0010"
down_revision = "20261010_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("local_credentials", sa.Column("password_change_required", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("local_credentials", "password_change_required")
