import argparse
import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter

from src.config import get_settings
from src.repositories import Store


class SiteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,59}$")
    name: str = Field(min_length=2, max_length=120)
    source_url: HttpUrl
    source_checked_on: date


def load_sites(path: Path) -> list[dict]:
    records = TypeAdapter(list[SiteRecord]).validate_json(path.read_text(encoding="utf-8"))
    if len({record.id for record in records}) != len(records):
        raise ValueError("duplicate_site_id")
    return [{**record.model_dump(), "source_url": str(record.source_url)} for record in records]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import sourced residential-site names without creating residents or granting access.")
    parser.add_argument("--file", type=Path, default=Path(__file__).with_name("residential_sites.json"))
    arguments = parser.parse_args()
    records = load_sites(arguments.file)
    store = Store(get_settings().require_database_url())
    try:
        store.initialize()
        imported = store.import_sites(records)
        print(json.dumps({"imported": imported, "sites": [record["name"] for record in records]}, ensure_ascii=False))
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    main()
