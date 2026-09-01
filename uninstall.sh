#!/usr/bin/env bash
set -Eeuo pipefail
APP="immich-auto-archive"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1
if [[ $EUID -ne 0 ]]; then echo "ERROR: Run as root." >&2; exit 1; fi
systemctl disable --now "$APP.timer" 2>/dev/null || true
rm -f "/etc/systemd/system/$APP.timer" "/etc/systemd/system/$APP.service"
systemctl daemon-reload
rm -f "/usr/local/bin/$APP"
rm -rf "/opt/$APP"
if [[ $PURGE -eq 1 ]]; then
  rm -rf "/etc/$APP"
  echo "Removed program and configuration."
else
  echo "Removed program. Configuration preserved in /etc/$APP"
  echo "Use: $0 --purge  to remove configuration and API keys too."
fi
