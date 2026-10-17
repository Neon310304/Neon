import sqlalchemy as sa

from alembic import op

revision = "20261015_0018"
down_revision = "20261015_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("local_credentials", sa.Column("username_change_required", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute("UPDATE local_credentials SET username_change_required = false WHERE password_change_required = false")


def downgrade() -> None:
    op.drop_column("local_credentials", "username_change_required")
