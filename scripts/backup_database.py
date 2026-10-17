import argparse
import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "residents",
    "checklist_tasks",
    "management_requests",
    "request_events",
    "parking_reminders",
    "knowledge_documents",
    "knowledge_chunks",
    "users",
    "residence_memberships",
    "security_audit_events",
    "revoked_auth_sessions",
)


def compose(*arguments: str, **options: Any) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *arguments], cwd=PROJECT_ROOT, check=True, **options)


def postgres_query(query: str, database_name: str = "") -> str:
    result = compose(
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'database="$1"; if [ -z "$database" ]; then database="$POSTGRES_DB"; fi; '
        'exec psql -U "$POSTGRES_USER" -d "$database" -At -v ON_ERROR_STOP=1 -c "$2"',
        "backup_database",
        database_name,
        query,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def postgres_counts(database_name: str = "") -> dict[str, int]:
    present = set(postgres_query(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'", database_name,
    ).splitlines())
    selected = [table_name for table_name in TABLES if table_name in present]
    if not selected:
        return {}
    fields = ", ".join(f"'{table_name}', (SELECT COUNT(*) FROM {table_name})" for table_name in selected)
    return json.loads(postgres_query(f"SELECT json_build_object({fields})", database_name))


def backup_postgres(destination: Path) -> None:
    with destination.open("xb") as output:
        compose(
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl',
            stdout=output,
        )
    with destination.open("rb") as backup_file:
        if backup_file.read(5) != b"PGDMP":
            raise RuntimeError("PostgreSQL backup is not a custom-format pg_dump archive")


def backup_sqlite(source: Path, destination: Path) -> dict[str, int]:
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == destination.resolve():
        raise ValueError("SQLite backup destination must differ from source")
    destination.touch(exist_ok=False)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as backup_connection:
            original.backup(backup_connection)
            if backup_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup failed integrity verification")
            present = {
                row[0] for row in backup_connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            return {
                table_name: backup_connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                for table_name in TABLES if table_name in present
            }


def verify_postgres_backup(backup_path: Path, expected_counts: dict[str, int]) -> None:
    temporary_database = "moveinmate_backup_check_" + uuid4().hex[:12]
    compose(
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'exec createdb -U "$POSTGRES_USER" "$1"',
        "backup_database",
        temporary_database,
    )
    try:
        with backup_path.open("rb") as backup_file:
            compose(
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'exec pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --exit-on-error',
                "backup_database",
                temporary_database,
                stdin=backup_file,
            )
        restored_counts = postgres_counts(temporary_database)
        if restored_counts != expected_counts:
            raise RuntimeError("Restored PostgreSQL row counts do not match the backup snapshot")
    finally:
        compose(
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'exec dropdb -U "$POSTGRES_USER" --if-exists "$1"',
            "backup_database",
            temporary_database,
        )


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(include_chroma: bool, verify_restore: bool) -> Path:
    created_at = datetime.now(timezone(timedelta(hours=7)))
    backup_directory = (
        PROJECT_ROOT / "data" / "backups" / (created_at.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    )
    backup_directory.mkdir(parents=True, exist_ok=False)
    should_restart = False
    if include_chroma:
        result = compose(
            "ps",
            "--status",
            "running",
            "-q",
            "backend",
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        should_restart = bool(result.stdout.strip())
    try:
        if should_restart:
            compose("stop", "backend")
        counts = postgres_counts()
        postgres_path = backup_directory / "postgres.dump"
        backup_postgres(postgres_path)
        manifest = {
            "created_at": created_at.isoformat(),
            "postgres": {
                "file": postgres_path.name,
                "sha256": file_hash(postgres_path),
                "counts": counts,
                "restore_verified": False,
            },
        }
        sqlite_source = PROJECT_ROOT / ".runtime" / "moveinmate.db"
        if sqlite_source.is_file():
            sqlite_path = backup_directory / "sqlite-source.db"
            sqlite_counts = backup_sqlite(sqlite_source, sqlite_path)
            manifest["sqlite"] = {
                "file": sqlite_path.name,
                "sha256": file_hash(sqlite_path),
                "counts": sqlite_counts,
            }
        if include_chroma:
            chroma_directory = backup_directory / "chroma"
            compose("cp", "backend:/app/.runtime/chroma", str(chroma_directory))
            manifest["chroma"] = {
                "directory": chroma_directory.name,
                "files": {
                    path.relative_to(chroma_directory).as_posix(): file_hash(path)
                    for path in sorted(chroma_directory.rglob("*"))
                    if path.is_file()
                },
            }
        if verify_restore:
            verify_postgres_backup(postgres_path, counts)
            manifest["postgres"]["restore_verified"] = True
        with (backup_directory / "manifest.json").open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write("\n")
    finally:
        if should_restart:
            compose("start", "--wait", "--wait-timeout", "180", "backend")
    return backup_directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up the Docker PostgreSQL database safely")
    parser.add_argument("--include-chroma", action="store_true")
    parser.add_argument("--verify-restore", action="store_true")
    arguments = parser.parse_args()
    try:
        backup_directory = create_backup(arguments.include_chroma, arguments.verify_restore)
    except (OSError, RuntimeError, sqlite3.Error, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Backup failed: {error}\n")
    print(json.dumps({"backup_directory": str(backup_directory)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
