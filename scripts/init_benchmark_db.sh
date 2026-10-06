#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "error: run this somewhere inside the git repository"
    exit 1
}

SCHEMA="$ROOT/harness/sql/schema.sql"
DB="${1:-$ROOT/results/benchmarks.sqlite3}"

command -v sqlite3 >/dev/null 2>&1 || {
    echo "error: sqlite3 is not installed"
    exit 1
}

mkdir -p "$(dirname "$DB")"

sqlite3 "$DB" <<SQL
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
.read $SCHEMA
SQL

echo
echo "Database initialized:"
echo "  $DB"
echo
echo "Tables:"
sqlite3 "$DB" ".tables"
echo
echo "Schema version:"
sqlite3 "$DB" \
    "SELECT version || ' - ' || description FROM schema_migrations ORDER BY version;"
