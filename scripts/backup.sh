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

# Public half of a keypair whose private half lives only on the operator's
# laptop. The server can encrypt a backup and cannot read one back - so a
# stolen disk, a mis-synced folder or a full server compromise yields
# ciphertext, not several people's health data.
#
# The trade-off is real and worth stating: lose that private key and every
# backup is permanently unreadable. There is no recovery path by design.
PUBKEY="${PUBKEY:-$HOME/.garmin-ai/backup-public.pem}"

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

if [ ! -f "$PUBKEY" ]; then
    # Refuse rather than fall back to plaintext. A backup silently written
    # unencrypted is worse than no backup: it looks like the protection is
    # in place while several people's health data sits readable on disk.
    echo "backup.sh: no public key at $PUBKEY - refusing to write an unencrypted backup" >&2
    exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/garmin_ai-$STAMP.sql.gz.enc"

# Dump -> compress -> encrypt, all streamed: the plaintext never touches the
# disk at any point, so there is no window in which an unencrypted copy
# exists to be read or recovered.
#
# Write to a .partial name first and rename only on success: an interrupted
# dump must never be left behind looking like a usable backup.
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    | gzip \
    | openssl cms -encrypt -aes-256-cbc -binary -outform DER -out "$TARGET.partial" "$PUBKEY"

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

find "$BACKUP_DIR" -name 'garmin_ai-*.sql.gz.enc' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name '*.partial' -mtime +1 -delete

echo "backup.sh: wrote $TARGET (${SIZE} bytes, encrypted)"
