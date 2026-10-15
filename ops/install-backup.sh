#!/usr/bin/env bash
# Install or update the backup on the Pi (run as root from the repo checkout):
#   sudo ops/install-backup.sh
# Re-run after every `git pull` that touches ops/. It never touches the Proton session.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
repo="$(cd "$(dirname "$0")/.." && pwd)"
env_file=/etc/velotheque/backup.env

install -d -m 755 /etc/velotheque
if [ ! -f "$env_file" ]; then
  sed "s|^VELOTHEQUE_DIR=.*|VELOTHEQUE_DIR=$repo|" "$repo/ops/backup.env.example" > "$env_file"
  chmod 644 "$env_file"
  echo "created $env_file: review VELOTHEQUE_DATA_DIR and the credentials store, then re-run"
fi
set -a; . "$env_file"; set +a
: "${BACKUP_USER:=velobackup}" "${STATE_DIR:=/var/lib/velotheque-backup}"

if ! id "$BACKUP_USER" > /dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$BACKUP_USER"
  echo "created system user $BACKUP_USER (home $STATE_DIR)"
fi
install -d -o "$BACKUP_USER" -g "$BACKUP_USER" -m 700 "$STATE_DIR"

lib=/usr/local/lib/velotheque-backup
install -d -m 755 "$lib"
install -m 755 "$repo/ops/dump-db.sh" "$repo/ops/record-status.sh" "$repo/ops/proton_backup.py" "$lib/"

for unit in velotheque-backup.service velotheque-backup.timer; do
  sed -e "s|^User=velobackup|User=$BACKUP_USER|" -e "s|^Group=velobackup|Group=$BACKUP_USER|" \
      -e "s|/var/lib/velotheque-backup|$STATE_DIR|g" \
      "$repo/ops/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now velotheque-backup.timer
systemctl list-timers velotheque-backup.timer --no-pager
echo
echo "next: log the backup user in to Proton Drive (README → Backup), then test with"
echo "  sudo systemctl start velotheque-backup && journalctl -u velotheque-backup -n 50"
