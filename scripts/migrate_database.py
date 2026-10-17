import argparse
import hashlib
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import AddConstraint

from alembic import command
from src.config import get_settings
from src.repositories.store import Base, Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "20261001_0001"
CORE_TABLES = (
    "residents", "checklist_tasks", "management_requests", "request_events", "parking_reminders",
)
MIGRATION_LOCK = 0x4D494D10


def validate_backup(path: Path) -> None:
    with path.open("rb") as backup:
        if backup.read(5) != b"PGDMP":
            raise ValueError("backup_must_be_postgresql_custom_format")
        backup.seek(0)
        digest = hashlib.file_digest(backup, "sha256").hexdigest()
    manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    snapshot = manifest.get("postgres", {})
    if (snapshot.get("file") != path.name or snapshot.get("sha256") != digest
            or snapshot.get("restore_verified") is not True):
        raise ValueError("backup_must_have_matching_checksum_and_verified_restore")


def align_legacy_schema(connection: sa.Connection) -> None:
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    inspector = sa.inspect(connection)
    present = set(inspector.get_table_names(schema=schema))
    allowed = set(CORE_TABLES) | {"knowledge_documents", "knowledge_chunks", "alembic_version"}
    if not set(CORE_TABLES).issubset(present) or present - allowed:
        raise ValueError("legacy_schema_not_recognized")
    columns = {
        table_name: {
            column["name"]: column
            for column in inspector.get_columns(table_name, schema=schema)
        }
        for table_name in CORE_TABLES
    }
    for table_name in CORE_TABLES:
        later_columns = {"building", "ownership_confirmed_at"} if table_name == "residents" else {"not_needed_at"} if table_name == "checklist_tasks" else set()
        expected = set(Base.metadata.tables[table_name].columns.keys()) - later_columns
        missing = expected - columns[table_name].keys()
        if missing - ({"business_key"} if table_name == "management_requests" else set()):
            raise ValueError("legacy_schema_missing_columns")
    connection.execute(sa.text(
        "LOCK TABLE residents, checklist_tasks, management_requests, request_events, "
        "parking_reminders IN ACCESS EXCLUSIVE MODE"
    ))
    operations = Operations(MigrationContext.configure(connection))
    for table_name in reversed(CORE_TABLES):
        for constraint in inspector.get_foreign_keys(table_name, schema=schema):
            operations.drop_constraint(constraint["name"], table_name, type_="foreignkey")
    request_columns = columns["management_requests"]
    if "business_key" not in request_columns:
        operations.add_column("management_requests", sa.Column("business_key", sa.String(64)))
    requests = list(connection.execute(sa.text(
        "SELECT id, request_type, payload_json FROM management_requests"
    )).mappings())
    for request in requests:
        payload = request["payload_json"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as error:
                raise ValueError("legacy_payload_not_valid_json") from error
        if not isinstance(payload, dict):
            raise ValueError("legacy_payload_must_be_object")
        connection.execute(sa.text(
            "UPDATE management_requests SET business_key = :business_key WHERE id = :request_id"
        ), {
            "business_key": Store._business_key(request["request_type"], payload),
            "request_id": request["id"],
        })
    if not isinstance(request_columns["payload_json"]["type"], JSONB):
        operations.alter_column(
            "management_requests", "payload_json", type_=JSONB(),
            postgresql_using="payload_json::jsonb",
        )
    operations.alter_column("management_requests", "business_key", nullable=False)
    for table_name in CORE_TABLES:
        target = Base.metadata.tables[table_name]
        for column in target.columns:
            if column.name in {"business_key", "building", "ownership_confirmed_at"} or column.name == "not_needed_at" and column.name not in columns[table_name]:
                continue
            current = columns[table_name][column.name]
            if isinstance(column.type, sa.BigInteger) and not isinstance(current["type"], sa.BigInteger):
                operations.alter_column(table_name, column.name, type_=sa.BigInteger())
            if isinstance(column.type, sa.DateTime) and column.type.timezone and not current["type"].timezone:
                operations.alter_column(
                    table_name, column.name, type_=column.type,
                    postgresql_using=f"{column.name} AT TIME ZONE 'UTC'",
                )
            if column.server_default is not None and column.identity is None:
                operations.alter_column(table_name, column.name, server_default=column.server_default.arg)
        for constraint in inspector.get_unique_constraints(table_name, schema=schema):
            operations.drop_constraint(constraint["name"], table_name, type_="unique")
        for constraint in inspector.get_check_constraints(table_name, schema=schema):
            operations.drop_constraint(constraint["name"], table_name, type_="check")
        for constraint in target.constraints:
            if isinstance(constraint, (sa.UniqueConstraint, sa.CheckConstraint)):
                connection.execute(AddConstraint(constraint))
        for index in target.indexes:
            index.create(bind=connection, checkfirst=True)
    for table_name in CORE_TABLES:
        for constraint in Base.metadata.tables[table_name].foreign_key_constraints:
            connection.execute(AddConstraint(constraint))
    for table_name in ("checklist_tasks", "request_events"):
        if columns[table_name]["id"].get("identity"):
            continue
        old_sequence = connection.scalar(sa.text("SELECT pg_get_serial_sequence(:table_name, 'id')"), {
            "table_name": table_name,
        })
        operations.alter_column(table_name, "id", server_default=None)
        if old_sequence:
            connection.execute(sa.text(f"ALTER SEQUENCE {old_sequence} OWNED BY NONE"))
        connection.execute(sa.text(f"ALTER TABLE {table_name} ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY"))
        connection.execute(sa.text(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"GREATEST(COALESCE(MAX(id), 1), 1), COUNT(*) > 0) FROM {table_name}"
        ))
        if old_sequence:
            connection.execute(sa.text(f"DROP SEQUENCE {old_sequence}"))


def migrate_connection(connection: sa.Connection, *, adopt_legacy: bool = False) -> None:
    if connection.dialect.name != "postgresql":
        raise ValueError("database_must_be_postgresql")
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": MIGRATION_LOCK})
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names(schema=schema))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.attributes["connection"] = connection
    config.attributes["version_table_schema"] = schema
    if "alembic_version" not in tables and tables.intersection(CORE_TABLES):
        if not adopt_legacy:
            raise ValueError("legacy_database_requires_adopt_flag_and_verified_backup")
        align_legacy_schema(connection)
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade PostgreSQL without seeding or deleting business data.")
    parser.add_argument("--adopt-legacy", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    arguments = parser.parse_args()
    if arguments.adopt_legacy:
        if arguments.backup_path is None:
            parser.error("--adopt-legacy requires --backup-path to an existing pg_dump archive")
        try:
            validate_backup(arguments.backup_path)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    settings = get_settings()
    engine = sa.create_engine(settings.require_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            migrate_connection(connection, adopt_legacy=arguments.adopt_legacy)
    finally:
        engine.dispose()
    print("PostgreSQL migrations complete; no demo data was added.")


if __name__ == "__main__":
    main()
