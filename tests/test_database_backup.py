import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_database
from scripts.migrate_database import validate_backup


@pytest.mark.parametrize("restore_verified", [True, False])
def test_legacy_migration_requires_verified_backup_manifest(tmp_path, restore_verified):
    archive = tmp_path / "postgres.dump"
    archive.write_bytes(b"PGDMP\x00isolated unit test")
    manifest = {"postgres": {
        "file": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "restore_verified": restore_verified,
    }}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if restore_verified:
        validate_backup(archive)
    else:
        with pytest.raises(ValueError, match="verified_restore"):
            validate_backup(archive)


def test_legacy_migration_refuses_changed_backup(tmp_path):
    archive = tmp_path / "postgres.dump"
    archive.write_bytes(b"PGDMP\x00changed archive")
    (tmp_path / "manifest.json").write_text(json.dumps({"postgres": {
        "file": archive.name, "sha256": "0" * 64, "restore_verified": True,
    }}), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        validate_backup(archive)


def test_sqlite_backup_preserves_rows_and_source(tmp_path: Path):
    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        for table_name in backup_database.TABLES:
            connection.execute(f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)')
            connection.execute(f'INSERT INTO "{table_name}" VALUES (1)')
    source_bytes = source.read_bytes()
    counts = backup_database.backup_sqlite(source, destination)
    assert counts == dict.fromkeys(backup_database.TABLES, 1)
    assert source.read_bytes() == source_bytes
    assert destination.is_file()


def test_sqlite_backup_does_not_create_a_missing_source(tmp_path: Path):
    source = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        backup_database.backup_sqlite(source, tmp_path / "backup.db")
    assert not source.exists()


def test_sqlite_legacy_backup_accepts_missing_new_auth_tables(tmp_path: Path):
    source = tmp_path / "legacy.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE residents (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO residents VALUES (1)")
    assert backup_database.backup_sqlite(source, tmp_path / "backup.db") == {"residents": 1}


def test_postgres_backup_counts_include_auth_and_knowledge(monkeypatch):
    tables = "residents\nknowledge_documents\nknowledge_chunks\nusers\n"
    expected = {"residents": 1, "knowledge_documents": 4, "knowledge_chunks": 914, "users": 0}

    def query(sql, database_name):
        assert database_name == "unit-test-db"
        if "pg_tables" in sql:
            return tables
        assert "knowledge_documents" in sql and "users" in sql
        assert "residence_memberships" not in sql
        return '{"residents": 1, "knowledge_documents": 4, "knowledge_chunks": 914, "users": 0}'

    monkeypatch.setattr(backup_database, "postgres_query", query)
    assert backup_database.postgres_counts("unit-test-db") == expected


def test_sqlite_backup_refuses_to_overwrite_source(tmp_path: Path):
    source = tmp_path / "source.db"
    source.touch()
    with pytest.raises(ValueError):
        backup_database.backup_sqlite(source, source)


def test_postgres_dump_uses_binary_output_and_refuses_overwrite(tmp_path: Path, monkeypatch):
    destination = tmp_path / "postgres.dump"

    def fake_compose(*arguments, **options):
        assert "--format=custom" in arguments[-1]
        assert "text" not in options
        options["stdout"].write(b"PGDMP\x00test archive")

    monkeypatch.setattr(backup_database, "compose", fake_compose)
    backup_database.backup_postgres(destination)
    assert destination.read_bytes().startswith(b"PGDMP")
    with pytest.raises(FileExistsError):
        backup_database.backup_postgres(destination)


@pytest.mark.parametrize("restored_matches", [True, False])
def test_restore_verification_uses_temporary_database_and_cleans_up(
    tmp_path: Path, monkeypatch, restored_matches: bool
):
    backup_path = tmp_path / "postgres.dump"
    backup_path.write_bytes(b"PGDMP\x00test archive")
    expected_counts = dict.fromkeys(backup_database.TABLES, 1)
    calls = []

    def fake_compose(*arguments, **options):
        calls.append(arguments)
        if "stdin" in options:
            assert options["stdin"].read() == backup_path.read_bytes()

    def fake_counts(database_name):
        assert database_name.startswith("moveinmate_backup_check_")
        return expected_counts if restored_matches else dict.fromkeys(backup_database.TABLES, 2)

    monkeypatch.setattr(backup_database, "compose", fake_compose)
    monkeypatch.setattr(backup_database, "postgres_counts", fake_counts)
    if restored_matches:
        backup_database.verify_postgres_backup(backup_path, expected_counts)
    else:
        with pytest.raises(RuntimeError, match="row counts"):
            backup_database.verify_postgres_backup(backup_path, expected_counts)
    assert any("createdb" in str(arguments) for arguments in calls)
    assert any("pg_restore" in str(arguments) for arguments in calls)
    assert "dropdb" in str(calls[-1])
    assert calls[0][-1] == calls[-1][-1]


@pytest.mark.parametrize("backend_running", [True, False])
def test_chroma_backup_preserves_backend_state_when_backup_fails(tmp_path: Path, monkeypatch, backend_running: bool):
    calls = []

    def fake_compose(*arguments, **options):
        calls.append(arguments)
        return SimpleNamespace(stdout="backend-container" if backend_running else "")

    def fail_counts():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(backup_database, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(backup_database, "compose", fake_compose)
    monkeypatch.setattr(backup_database, "postgres_counts", fail_counts)
    with pytest.raises(RuntimeError, match="database unavailable"):
        backup_database.create_backup(include_chroma=True, verify_restore=False)
    assert (("stop", "backend") in calls) is backend_running
    assert any(arguments[0] == "start" for arguments in calls) is backend_running
