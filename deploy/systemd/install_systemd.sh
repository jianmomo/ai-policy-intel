#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-/data/ai-policy-intel}"
SYSTEMD_DIR="/etc/systemd/system"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

for unit in \
  ai-policy-intel-api.service \
  ai-policy-intel-daily.service \
  ai-policy-intel-daily.timer \
  ai-policy-intel-weekly.service \
  ai-policy-intel-weekly.timer \
  ai-policy-intel-backup.service \
  ai-policy-intel-backup.timer \
  ai-policy-intel-policy-refresh.service \
  ai-policy-intel-policy-refresh.timer
 do
  sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/deploy/systemd/$unit" > "$TMP_DIR/$unit"
  cp "$TMP_DIR/$unit" "$SYSTEMD_DIR/$unit"
done

systemctl daemon-reload
systemctl enable ai-policy-intel-api.service
systemctl enable ai-policy-intel-daily.timer
systemctl enable ai-policy-intel-weekly.timer
systemctl enable ai-policy-intel-backup.timer
systemctl enable ai-policy-intel-policy-refresh.timer
systemctl restart ai-policy-intel-api.service
systemctl restart ai-policy-intel-daily.timer
systemctl restart ai-policy-intel-weekly.timer
systemctl restart ai-policy-intel-backup.timer
systemctl restart ai-policy-intel-policy-refresh.timer

echo "Systemd units installed for $PROJECT_DIR"
