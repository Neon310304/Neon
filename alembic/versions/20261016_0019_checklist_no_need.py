from alembic import op

revision = "20261016_0019"
down_revision = "20261015_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE checklist_tasks ADD COLUMN IF NOT EXISTS not_needed_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE checklist_tasks DROP COLUMN IF EXISTS not_needed_at")
