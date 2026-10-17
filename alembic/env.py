import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The PostgreSQL baseline intentionally precedes the Store model conversion in
# DB-MVP-04/05. Keep migrations explicit until those ORM models match it.
target_metadata = None


def database_url() -> str:
    raw_url = os.environ.get("DATABASE_URL", "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is required for Alembic")
    try:
        url = make_url(raw_url)
    except ArgumentError as exc:
        raise RuntimeError("DATABASE_URL is invalid") from exc
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Alembic migrations require PostgreSQL")
    host_override = os.environ.get("DB_HOST_OVERRIDE", "").strip()
    if host_override:
        url = url.set(host=host_override)
    return url.render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=config.attributes.get("version_table_schema"),
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    escaped_url = database_url().replace("%", "%%")
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = escaped_url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
