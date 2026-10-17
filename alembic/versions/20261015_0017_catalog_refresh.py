import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from alembic import op

revision = "20261015_0017"
down_revision = "20261014_0016"
branch_labels = None
depends_on = None

SITES = (
    ("vinhomes-ocean-park", "Vinhomes Ocean Park", "https://oceanpark.vinhomes.vn/", "2026-10-09"),
    ("vinhomes-smart-city", "Vinhomes Smart City", "https://smartcity.vinhomes.vn/", "2026-10-09"),
    ("vinhomes-grand-park", "Vinhomes Grand Park", "https://vinhomes.vn/vi/vinhomes-grand-park", "2026-10-09"),
    ("vinhomes-central-park", "Vinhomes Central Park", "https://market.vinhomes.vn/blog/cac-du-an-cua-vinhomes", "2026-10-14"),
    ("vinhomes-riverside", "Vinhomes Riverside", "https://market.vinhomes.vn/en/blog/vinhomes-riverside", "2026-10-14"),
    ("vinhomes-times-city", "Vinhomes Times City", "https://market.vinhomes.vn/blog/vinhomes-times-city", "2026-10-14"),
    ("vinhomes-royal-city", "Vinhomes Royal City", "https://market.vinhomes.vn/en/blog/vinhomes-royal-city", "2026-10-14"),
    ("masteri-thao-dien", "Masteri Thảo Điền", "https://masterisehomes.com/masteri-thao-dien", "2026-10-14"),
    ("masteri-an-phu", "Masteri An Phú", "https://masterisehomes.com/masteri-an-phu", "2026-10-14"),
)


def upgrade() -> None:
    from datetime import date

    catalog = sa.table("residential_sites", sa.column("id"), sa.column("name"), sa.column("source_url"), sa.column("source_checked_on"))
    for site_id, name, source, checked in SITES:
        statement = insert(catalog).values(id=site_id, name=name, source_url=source, source_checked_on=date.fromisoformat(checked))
        op.execute(statement.on_conflict_do_update(index_elements=[catalog.c.id], set_={
            "name": statement.excluded.name, "source_url": statement.excluded.source_url,
            "source_checked_on": statement.excluded.source_checked_on,
        }))
    op.execute("""
        INSERT INTO residential_units (site_id, building, floor, room, apartment)
        SELECT sites.id, towers.building, floors.floor, rooms.room,
               towers.building || lpad(floors.floor::text, 2, '0') || lpad(rooms.room::text, 2, '0')
        FROM residential_sites AS sites
        CROSS JOIN unnest(ARRAY['A', 'B', 'C', 'D']::text[]) AS towers(building)
        CROSS JOIN generate_series(1, 20) AS floors(floor)
        CROSS JOIN generate_series(1, 10) AS rooms(room)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    return None
