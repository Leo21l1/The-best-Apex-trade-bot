#!/usr/bin/env bash
# Timestamped, compressed PostgreSQL backup. Usage: scripts/backup-db.sh [output-dir]
# Reads standard libpq env vars (PGHOST, PGUSER, PGPASSWORD, PGDATABASE) or the compose defaults.
set -euo pipefail
OUT="${1:-./backups}"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT/apex-${PGDATABASE:-apex}-$STAMP.dump"
pg_dump --format=custom --no-owner \
  --host "${PGHOST:-localhost}" --username "${PGUSER:-apex}" "${PGDATABASE:-apex}" > "$FILE"
chmod 600 "$FILE"
echo "Backup written to $FILE (restore with: pg_restore --clean -d <db> $FILE)"
