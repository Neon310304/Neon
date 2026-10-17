from alembic import op

revision = "20261013_0012"
down_revision = "20261013_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE residents ADD COLUMN IF NOT EXISTS ownership_confirmed_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE residents DROP COLUMN IF EXISTS ownership_confirmed_at")
