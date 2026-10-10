#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
DB="${1:-$ROOT/results/benchmarks.sqlite3}"
SCHEMA="$ROOT/harness/sql/schema.sql"

mkdir -p "$(dirname "$DB")"

if [[ -e "$DB" && -s "$DB" ]]; then
    echo "ERROR: database already exists and is non-empty:"
    echo "  $DB"
    echo
    echo "This script only creates new benchmark databases."
    echo "Existing databases are upgraded automatically by threadance-harness."
    exit 1
fi

rm -f "${DB}-wal" "${DB}-shm"

sqlite3 "$DB" <<SQL
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
.read $SCHEMA
SQL

VERSIONS="$(
    sqlite3 "$DB" "
        SELECT group_concat(version, ',')
        FROM (
            SELECT version
            FROM schema_migrations
            ORDER BY version
        );
    "
)"

if [[ "$VERSIONS" != "1,2,3,4,5,6,7" ]]; then
    echo "ERROR: unexpected migration history: $VERSIONS"
    exit 1
fi

FK_ERRORS="$(sqlite3 "$DB" 'PRAGMA foreign_key_check;')"

if [[ -n "$FK_ERRORS" ]]; then
    echo "ERROR: foreign-key check failed:"
    echo "$FK_ERRORS"
    exit 1
fi

echo
echo "Database initialized:"
echo "  $DB"

echo
echo "Schema migrations:"
sqlite3 "$DB" \
    'SELECT version || " - " || description
     FROM schema_migrations
     ORDER BY version;'
