#!/usr/bin/env bash
#
# Nightly Postgres backup for the production deployment.
#
# Installed on the server as a systemd timer (see scripts/install-backup.sh).
# Run manually with:  ~/garmin-ai/scripts/backup.sh
#
# Dumps through the running db container rather than a host psql client, so
# there is no need to install Postgres tooling on the host and no risk of a
# client/server version mismatch.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/garmin-ai}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE_FILE="docker-compose.prod.yml"

cd "$PROJECT_DIR"

# The dump holds health data and encrypted Garmin tokens - keep the directory
# readable only by its owner, the same discipline as .env.
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Credentials come from .env rather than being duplicated here, so rotating
# the password in one place stays sufficient.
set -a
# shellcheck disable=SC1091
source .env
set +a

STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/garmin_ai-$STAMP.sql.gz"

# Write to a .partial name first and rename only on success: an interrupted
# dump must never be left behind looking like a usable backup.
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    | gzip > "$TARGET.partial"

mv "$TARGET.partial" "$TARGET"
chmod 600 "$TARGET"

# Refuse to call a suspiciously small dump a success - an empty or truncated
# file is worse than a loud failure, because it hides the problem until a
# restore is actually needed.
SIZE=$(stat -c %s "$TARGET")
if [ "$SIZE" -lt 1024 ]; then
    echo "backup.sh: dump is only ${SIZE} bytes - treating as failure" >&2
    exit 1
fi

find "$BACKUP_DIR" -name 'garmin_ai-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name '*.partial' -mtime +1 -delete

echo "backup.sh: wrote $TARGET (${SIZE} bytes)"
