import sqlalchemy as sa

from alembic import op

revision = "20261009_0006"
down_revision = "20261009_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "residential_sites",
        sa.Column("id", sa.String(60), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("source_checked_on", sa.Date(), nullable=True),
    )
    op.execute("INSERT INTO residential_sites (id, name) SELECT site_id, min(site_name) FROM residents GROUP BY site_id")


def downgrade() -> None:
    op.drop_table("residential_sites")
