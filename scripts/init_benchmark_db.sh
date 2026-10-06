#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
DB="${1:-$ROOT/results/benchmarks.sqlite3}"

SCHEMA="$ROOT/harness/sql/schema.sql"
MIGRATION="$ROOT/harness/sql/migrations/002_perf_passes.sql"

mkdir -p "$(dirname "$DB")"

sqlite3 "$DB" <<SQL
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
.read $SCHEMA
SQL

HAS_PASS="$(sqlite3 "$DB" \
    "SELECT COUNT(*)
     FROM pragma_table_info('run')
     WHERE name = 'perf_pass';")"

if [[ "$HAS_PASS" == "0" ]]; then
    echo "Applying perf-pass migration..."

    sqlite3 "$DB" \
        "DELETE FROM schema_migrations WHERE version = 2;"

    sqlite3 "$DB" < "$MIGRATION"
fi

echo
echo "Database initialized:"
echo "  $DB"

echo
echo "Schema:"
sqlite3 "$DB" \
    'SELECT version || " - " || description
     FROM schema_migrations
     ORDER BY version;'
