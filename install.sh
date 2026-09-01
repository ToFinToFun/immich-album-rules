#!/usr/bin/env bash
set -Eeuo pipefail

APP="immich-auto-archive"
INSTALL_DIR="/opt/${APP}"
CONFIG_DIR="/etc/${APP}"
BIN="/usr/local/bin/${APP}"
SERVICE="/etc/systemd/system/${APP}.service"
TIMER="/etc/systemd/system/${APP}.timer"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Run this installer as root." >&2
  exit 1
fi

for cmd in python3 systemctl install; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: Required command missing: $cmd" >&2; exit 1; }
done

if [[ ! -f "$SOURCE_DIR/src/immich_auto_archive.py" ]]; then
  echo "ERROR: Run install.sh from the project directory." >&2
  exit 1
fi

echo "Installing Immich Auto Archive..."
install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$SOURCE_DIR/src/immich_auto_archive.py" "$INSTALL_DIR/immich_auto_archive.py"

# Configuration and API keys intentionally live outside /opt/immich and are preserved on upgrades.
install -d -m 0700 "$CONFIG_DIR" "$CONFIG_DIR/keys"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "version": 1,
  "server_url": "http://127.0.0.1:2283/api",
  "sync_interval_minutes": 5,
  "default_albums": [
    "Screenshots",
    "Download",
    "WhatsApp",
    "WhatsApp Images",
    "WhatsApp Video",
    "Facebook",
    "Messenger",
    "Messages"
  ],
  "users": {}
}
JSON
  chmod 0600 "$CONFIG_DIR/config.json"
fi

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/immich_auto_archive.py" "\$@"
EOF
chmod 0755 "$BIN"

install -m 0644 "$SOURCE_DIR/systemd/${APP}.service" "$SERVICE"
install -m 0644 "$SOURCE_DIR/systemd/${APP}.timer" "$TIMER"

systemctl daemon-reload
systemctl enable --now "${APP}.timer"

# Populate users, but don't fail install solely because Immich is temporarily stopped.
"$BIN" --refresh-users || true

echo
echo "Installed successfully."
echo "  Menu:    $APP"
echo "  Doctor:  $APP --doctor"
echo "  Dry-run: $APP --dry-run"
echo "  Sync:    $APP --sync"
echo
echo "Configuration and keys are preserved when install.sh is run again."
