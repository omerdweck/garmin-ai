#!/usr/bin/env bash
#
# Installs the nightly backup as a systemd timer. Run once on the server:
#   sudo ~/garmin-ai/scripts/install-backup.sh
#
# systemd rather than cron: a timer records its last run and its exit status
# in systemctl/journalctl, so "did last night's backup actually work?" has an
# answer. A cron job that fails silently looks identical to one that never ran.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "install-backup.sh: must run as root (use sudo)" >&2
    exit 1
fi

RUN_AS="${SUDO_USER:-garmin}"
HOME_DIR="$(getent passwd "$RUN_AS" | cut -d: -f6)"

cat > /etc/systemd/system/garmin-backup.service <<EOF
[Unit]
Description=Nightly Postgres backup for Garmin AI
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$RUN_AS
WorkingDirectory=$HOME_DIR/garmin-ai
ExecStart=$HOME_DIR/garmin-ai/scripts/backup.sh
EOF

cat > /etc/systemd/system/garmin-backup.timer <<EOF
[Unit]
Description=Run the Garmin AI backup nightly

[Timer]
OnCalendar=*-*-* 03:30:00
# Survive downtime: if the server was off at 03:30, run once it is back
# rather than skipping that night entirely.
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now garmin-backup.timer

echo "--- timer installed ---"
systemctl list-timers garmin-backup --no-pager
