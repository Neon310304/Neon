#!/bin/sh
set -eu

if [ -z "${POSTGRES_TEST_DB:-}" ]; then
    echo "POSTGRES_TEST_DB is required" >&2
    exit 1
fi

if [ "$POSTGRES_TEST_DB" = "$POSTGRES_DB" ]; then
    echo "POSTGRES_TEST_DB must differ from POSTGRES_DB" >&2
    exit 1
fi

psql --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set test_database="$POSTGRES_TEST_DB" <<'SQL'
SELECT format('CREATE DATABASE %I', :'test_database')
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = :'test_database'
)\gexec
SQL
