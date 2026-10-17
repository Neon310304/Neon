import sqlalchemy as sa

from alembic import op

revision = "20261014_0013"
down_revision = "20261013_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "residential_units",
        sa.Column("site_id", sa.String(length=60), nullable=False),
        sa.Column("building", sa.String(length=1), nullable=False),
        sa.Column("floor", sa.Integer(), nullable=False),
        sa.Column("room", sa.Integer(), nullable=False),
        sa.Column("apartment", sa.String(length=8), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["residential_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id", "building", "floor", "room"),
        sa.UniqueConstraint("site_id", "apartment", name="uq_residential_units_site_apartment"),
        sa.CheckConstraint("building IN ('A', 'B', 'C', 'D')", name="ck_residential_units_building"),
        sa.CheckConstraint("floor BETWEEN 1 AND 20", name="ck_residential_units_floor_range"),
        sa.CheckConstraint("room BETWEEN 1 AND 10", name="ck_residential_units_room_range"),
    )
    op.create_index("ix_residential_units_site_apartment", "residential_units", ["site_id", "apartment"])
    op.execute("""
        INSERT INTO residential_sites (id, name)
        SELECT DISTINCT ON (site_id) site_id, site_name FROM residents
        ORDER BY site_id, id
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute("""
        INSERT INTO residential_units (site_id, building, floor, room, apartment)
        SELECT sites.id, towers.building, floors.floor, rooms.room,
               towers.building || lpad(floors.floor::text, 2, '0') || lpad(rooms.room::text, 2, '0')
        FROM residential_sites AS sites
        CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D']::text[]) AS towers(building)
        CROSS JOIN generate_series(1, 20) AS floors(floor)
        CROSS JOIN generate_series(1, 10) AS rooms(room)
    """)


def downgrade() -> None:
    op.drop_index("ix_residential_units_site_apartment", table_name="residential_units")
    op.drop_table("residential_units")
