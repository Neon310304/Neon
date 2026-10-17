import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB

from scripts.migrate_database import CORE_TABLES, migrate_connection
from src.repositories.knowledge import KnowledgeChunk, KnowledgeDocument
from src.repositories.store import Base, Store


@pytest.fixture(params=[False, True], ids=["without_ownership_column", "with_ownership_column"])
def legacy_database(postgres_test_database_url, request):
    engine = sa.create_engine(postgres_test_database_url)
    schema = "legacy_adoption_" + uuid4().hex
    with engine.connect() as connection:
        assert "test" in connection.scalar(sa.text("SELECT current_database()"))
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        connection.commit()
        metadata = sa.MetaData()
        for table_name in CORE_TABLES:
            target = Base.metadata.tables[table_name]
            columns = []
            for column in target.columns:
                if column.name in {"business_key", "building"} or (column.name == "ownership_confirmed_at" and not request.param):
                    continue
                column_type = column.type
                if isinstance(column_type, sa.BigInteger):
                    column_type = sa.Integer()
                if column.name == "payload_json":
                    column_type = sa.Text()
                columns.append(sa.Column(
                    column.name, column_type, nullable=column.nullable,
                    primary_key=column.primary_key,
                ))
            table = sa.Table(table_name, metadata, *columns)
            for constraint in target.foreign_key_constraints:
                if len(constraint.columns) == 1:
                    table.append_constraint(sa.ForeignKeyConstraint(
                        [column.name for column in constraint.columns],
                        [element.target_fullname for element in constraint.elements],
                    ))
            if table_name == "management_requests":
                table.append_constraint(sa.UniqueConstraint("idempotency_key"))
        KnowledgeDocument.__table__.to_metadata(metadata)
        KnowledgeChunk.__table__.to_metadata(metadata)
        with connection.begin():
            metadata.create_all(connection)
            created_at = datetime(2026, 1, 1, tzinfo=UTC)
            connection.execute(metadata.tables["residents"].insert(), {
                "id": "UNIT-LEGACY", "full_name": "Isolated unit test resident", "apartment": "TEST-01",
                "site_id": "unit-test-site", "site_name": "Unit test", "resident_type": "owner",
                "family_members": 1, "vehicle": "none", "needs_internet": False,
                "profile_version": 3, "updated_at": created_at,
            })
            connection.execute(metadata.tables["checklist_tasks"].insert(), {
                "id": 17, "resident_id": "UNIT-LEGACY", "code": "resident_card", "title": "Test card",
                "required": True, "status": "submitted", "reason": "Unit test", "active": True,
                "updated_at": created_at,
            })
            connection.execute(metadata.tables["management_requests"].insert(), {
                "id": "REQ-LEGACY", "resident_id": "UNIT-LEGACY", "request_type": "resident_card",
                "status": "submitted", "payload_json": json.dumps({"details": "Test Unicode: cư dân"}),
                "decision_reason": None, "idempotency_key": "legacy-unit", "created_at": created_at,
                "updated_at": created_at,
            })
            connection.execute(metadata.tables["request_events"].insert(), {
                "id": 23, "request_id": "REQ-LEGACY", "status": "submitted", "reason": None,
                "created_at": created_at,
            })
            connection.execute(metadata.tables["knowledge_documents"].insert(), {
                "id": "UNIT-SOURCE", "source_url": "https://congbao.chinhphu.vn/unit-test.htm",
                "title": "Unit test, not a real legal document", "document_number": "UNIT-2026",
                "category": "unit", "publisher": "Unit fixture", "source_type": "unit_test",
                "legal_status": "not_verified", "content": "Preserve this test content exactly",
                "content_sha256": "a" * 64, "provenance_json": '{"files": []}',
                "fetched_at": created_at, "active": True,
            })
            connection.execute(metadata.tables["knowledge_chunks"].insert(), {
                "id": "UNIT-SOURCE:0", "document_id": "UNIT-SOURCE", "ordinal": 0,
                "pdf_url": "https://congbaocdn.chinhphu.vn/unit-test.pdf", "pdf_part": 1,
                "page_number": 1, "text": "Preserve this test chunk exactly", "text_sha256": "b" * 64,
            })
        try:
            yield connection, schema
        finally:
            connection.rollback()
            connection.exec_driver_sql("SET search_path TO public")
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            connection.commit()
    engine.dispose()


def test_legacy_adoption_preserves_rows_and_is_repeatable(legacy_database):
    connection, schema = legacy_database
    with connection.begin():
        original_source = dict(connection.execute(sa.text("SELECT * FROM knowledge_documents")).mappings().one())
        original_chunk = dict(connection.execute(sa.text("SELECT * FROM knowledge_chunks")).mappings().one())
        migrate_connection(connection, adopt_legacy=True)
        migrate_connection(connection)
        request = connection.execute(sa.text("SELECT * FROM management_requests")).mappings().one()
        assert request["payload_json"] == {"details": "Test Unicode: cư dân"}
        assert request["business_key"] == Store._business_key("resident_card", request["payload_json"])
        assert dict(connection.execute(sa.text("SELECT * FROM knowledge_documents")).mappings().one()) == original_source
        assert dict(connection.execute(sa.text("SELECT * FROM knowledge_chunks")).mappings().one()) == original_chunk
        assert connection.scalar(sa.text("SELECT profile_version FROM residents")) == 3
        assert connection.scalar(sa.text("SELECT id FROM checklist_tasks")) == 17
        assert connection.scalar(sa.text("SELECT id FROM request_events")) == 23
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        assert connection.scalar(sa.text("SELECT ownership_confirmed_at FROM residents")) is None
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM residential_units WHERE site_id = 'unit-test-site'")) == 800
        inspector = sa.inspect(connection)
        request_columns = {
            column["name"]: column for column in inspector.get_columns("management_requests", schema=schema)
        }
        assert isinstance(request_columns["payload_json"]["type"], JSONB)
        task_columns = {column["name"]: column for column in inspector.get_columns("checklist_tasks", schema=schema)}
        assert task_columns["id"]["identity"] is not None
        next_id = connection.scalar(sa.text("SELECT nextval(pg_get_serial_sequence('checklist_tasks', 'id'))"))
        assert next_id == 18


def test_legacy_adoption_requires_explicit_opt_in(legacy_database):
    connection, schema = legacy_database
    with pytest.raises(ValueError, match="adopt_flag"):
        with connection.begin():
            migrate_connection(connection)
    assert not sa.inspect(connection).has_table("alembic_version", schema=schema)


def test_invalid_legacy_payload_rolls_back_every_schema_change(legacy_database):
    connection, schema = legacy_database
    with connection.begin():
        connection.execute(sa.text("UPDATE management_requests SET payload_json = 'not-json'"))
    with pytest.raises(ValueError, match="valid_json"):
        with connection.begin():
            migrate_connection(connection, adopt_legacy=True)
    inspector = sa.inspect(connection)
    assert not inspector.has_table("alembic_version", schema=schema)
    columns = {column["name"]: column for column in inspector.get_columns("management_requests", schema=schema)}
    assert "business_key" not in columns
    assert isinstance(columns["payload_json"]["type"], sa.Text)
