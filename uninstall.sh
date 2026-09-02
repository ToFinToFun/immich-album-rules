#!/usr/bin/env bash
set -Eeuo pipefail

APP="immich-album-rules"
LEGACY_APP="immich-auto-archive"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Run as root." >&2
  exit 1
fi

for name in "$APP" "$LEGACY_APP"; do
  systemctl disable --now "$name.timer" 2>/dev/null || true
  systemctl stop "$name.service" 2>/dev/null || true
  rm -f "/etc/systemd/system/$name.timer" "/etc/systemd/system/$name.service"
done
systemctl daemon-reload

rm -f "/usr/local/bin/$APP" "/usr/local/bin/$LEGACY_APP"
rm -rf "/opt/$APP" "/opt/$LEGACY_APP"

if [[ $PURGE -eq 1 ]]; then
  rm -rf "/etc/$APP" "/etc/$LEGACY_APP"
  echo "Removed program, configuration and API keys."
else
  echo "Removed program. Configuration preserved in /etc/$APP"
  [[ -d "/etc/$LEGACY_APP" ]] && echo "Legacy configuration also remains in /etc/$LEGACY_APP"
  echo "Use: $0 --purge  to remove configuration and API keys too."
fi
