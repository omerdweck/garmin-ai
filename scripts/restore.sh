#!/usr/bin/env bash
#
# Decrypt a backup. Run this on the machine holding the PRIVATE key - by
# design that is not the server.
#
#   ./scripts/restore.sh garmin_ai-20260906-093000.sql.gz.enc
#
# Writes the decrypted dump next to the input and stops there. Loading it
# into a database is deliberately a separate, manual step: a restore script
# that overwrites a live database on one command is a foot-gun, and the
# moment you need it is the moment you are least careful.
#
# To load it afterwards, on the server:
#   gunzip -c garmin_ai-<stamp>.sql | docker compose -f docker-compose.prod.yml \
#       exec -T db psql -U garmin_app -d garmin_ai

set -euo pipefail

PRIVKEY="${PRIVKEY:-$HOME/.garmin-ai/backup-private.pem}"

if [ $# -ne 1 ]; then
    echo "usage: $0 <backup.sql.gz.enc>" >&2
    exit 1
fi

INPUT="$1"
OUTPUT="${INPUT%.enc}"

if [ ! -f "$PRIVKEY" ]; then
    echo "restore.sh: no private key at $PRIVKEY" >&2
    echo "  Without it these backups cannot be read - by anyone, including you." >&2
    exit 1
fi

if [ ! -f "$INPUT" ]; then
    echo "restore.sh: no such file: $INPUT" >&2
    exit 1
fi

openssl cms -decrypt -inform DER -binary -in "$INPUT" -inkey "$PRIVKEY" -out "$OUTPUT"

# Prove it decrypted to something real rather than to plausible-looking
# garbage: a gzip stream that will not unpack is a failed restore, and
# finding that out now beats finding it out mid-incident.
if ! gzip -t "$OUTPUT" 2>/dev/null; then
    echo "restore.sh: decrypted output is not a valid gzip stream" >&2
    rm -f "$OUTPUT"
    exit 1
fi

echo "restore.sh: wrote $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
echo "restore.sh: contains $(gunzip -c "$OUTPUT" | wc -l) lines of SQL"
