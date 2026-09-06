#!/usr/bin/env bash
#
# Deploy the current main branch to this server.
#   ~/garmin-ai/scripts/deploy.sh
#
# Exists because doing these steps by hand went wrong four times in one day,
# always the same way: `docker compose up -d` reported every service as
# "Started" while the containers kept running the previous image, so the
# deploy looked successful and the new code was simply absent. --force-recreate
# is the fix, and the verification at the end is what makes a silent failure
# impossible to mistake for success.

set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "==> pulling"
git pull --ff-only

echo "==> building"
$COMPOSE build

echo "==> migrating"
# Runs against the live database before the new code starts serving, so the
# schema is never behind the code that expects it.
$COMPOSE run --rm api alembic upgrade head

echo "==> recreating containers"
# --force-recreate, not plain `up -d`: compose can decide a container is
# already up to date and leave stale code running.
$COMPOSE up -d --force-recreate

echo "==> waiting for services"
sleep 12

echo "==> verifying what is ACTUALLY running"
EXPECTED=$(git rev-parse --short HEAD)
FAILED=0

for svc in bot worker api; do
    # Import the app inside the container: a container that starts but cannot
    # import its own modules is a broken deploy that `ps` still calls healthy.
    if $COMPOSE exec -T "$svc" python -c "import app.models, app.core.claude_client" >/dev/null 2>&1; then
        echo "    $svc: imports OK"
    else
        echo "    $svc: IMPORT FAILED"
        FAILED=1
    fi
done

DB_REV=$($COMPOSE exec -T db psql -U "${POSTGRES_USER:-garmin_app}" -d "${POSTGRES_DB:-garmin_ai}" \
         -qtA -c "SELECT version_num FROM alembic_version;" 2>/dev/null | tr -d '[:space:]')
HEAD_REV=$($COMPOSE run --rm api alembic heads 2>/dev/null | grep -oE '^[0-9a-f]{12}' | head -1)

echo "    migration in db: $DB_REV"
if [ -n "$HEAD_REV" ] && [ "$DB_REV" != "$HEAD_REV" ]; then
    echo "    MIGRATION BEHIND: code expects $HEAD_REV"
    FAILED=1
fi

echo "    git commit deployed: $EXPECTED"

if [ "$FAILED" -ne 0 ]; then
    echo "==> DEPLOY FAILED - see above"
    exit 1
fi

echo "==> deploy OK"
