#!/usr/bin/env bash
set -Eeuo pipefail

APP="immich-album-rules"
LEGACY_APP="immich-auto-archive"
INSTALL_DIR="/opt/${APP}"
CONFIG_DIR="/etc/${APP}"
BIN="/usr/local/bin/${APP}"
SERVICE="/etc/systemd/system/${APP}.service"
TIMER="/etc/systemd/system/${APP}.timer"
LEGACY_INSTALL_DIR="/opt/${LEGACY_APP}"
LEGACY_CONFIG_DIR="/etc/${LEGACY_APP}"
LEGACY_BIN="/usr/local/bin/${LEGACY_APP}"
LEGACY_SERVICE="/etc/systemd/system/${LEGACY_APP}.service"
LEGACY_TIMER="/etc/systemd/system/${LEGACY_APP}.timer"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Run this installer as root." >&2
  exit 1
fi

for cmd in python3 systemctl install; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: Required command missing: $cmd" >&2; exit 1; }
done

if [[ ! -f "$SOURCE_DIR/src/immich_album_rules.py" ]]; then
  echo "ERROR: Run install.sh from the Immich Album Rules project directory." >&2
  exit 1
fi

# v0.1.x / early v0.2 development builds were named immich-auto-archive.
# Move the whole protected config directory so API keys and rules survive the rename.
if [[ -d "$LEGACY_CONFIG_DIR" && ! -e "$CONFIG_DIR" ]]; then
  echo "Migrating configuration from $LEGACY_CONFIG_DIR to $CONFIG_DIR..."
  mv "$LEGACY_CONFIG_DIR" "$CONFIG_DIR"
elif [[ -d "$LEGACY_CONFIG_DIR" && -e "$CONFIG_DIR" ]]; then
  echo "NOTICE: Both old and new configuration directories exist." >&2
  echo "        Using $CONFIG_DIR and leaving $LEGACY_CONFIG_DIR untouched." >&2
fi

# Stop the old scheduler before replacing the application so two timers cannot act at once.
systemctl disable --now "${LEGACY_APP}.timer" >/dev/null 2>&1 || true
systemctl stop "${LEGACY_APP}.service" >/dev/null 2>&1 || true

echo "Installing Immich Album Rules..."
install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$SOURCE_DIR/src/immich_album_rules.py" "$INSTALL_DIR/immich_album_rules.py"

# Configuration and API keys intentionally live outside Immich's own directories.
install -d -m 0700 "$CONFIG_DIR" "$CONFIG_DIR/keys"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "version": 2,
  "server_url": "http://127.0.0.1:2283/api",
  "sync_interval_minutes": 5,
  "default_rules": [
    {"album": "Screenshots", "action": "archive"},
    {"album": "Download", "action": "archive"},
    {"album": "WhatsApp", "action": "archive"},
    {"album": "WhatsApp Images", "action": "archive"},
    {"album": "WhatsApp Video", "action": "archive"},
    {"album": "Facebook", "action": "archive"},
    {"album": "Messenger", "action": "archive"},
    {"album": "Messages", "action": "archive"}
  ],
  "users": {}
}
JSON
  chmod 0600 "$CONFIG_DIR/config.json"
fi

cat > "$BIN" <<EOF_BIN
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/immich_album_rules.py" "\$@"
EOF_BIN
chmod 0755 "$BIN"

install -m 0644 "$SOURCE_DIR/systemd/${APP}.service" "$SERVICE"
install -m 0644 "$SOURCE_DIR/systemd/${APP}.timer" "$TIMER"

# Remove obsolete old program units/files after configuration has been preserved.
rm -f "$LEGACY_SERVICE" "$LEGACY_TIMER"
rm -rf "$LEGACY_INSTALL_DIR"

# Keep the old command as a compatibility alias for existing scripts/bookmarks.
cat > "$LEGACY_BIN" <<EOF_COMPAT
#!/usr/bin/env bash
echo "NOTICE: '$LEGACY_APP' was renamed to '$APP'. Please update your command." >&2
exec "$BIN" "\$@"
EOF_COMPAT
chmod 0755 "$LEGACY_BIN"

systemctl daemon-reload
systemctl enable --now "${APP}.timer"

# Loading the program performs safe config schema migration from v0.1.x if needed.
"$BIN" --refresh-users || true

echo
echo "Installed successfully."
echo "  Menu:    $APP"
echo "  Doctor:  $APP --doctor"
echo "  Dry-run: $APP --dry-run"
echo "  Sync:    $APP --sync"
echo
echo "Configuration and API keys are preserved across upgrades."
echo "Existing Immich Auto Archive installations are migrated automatically."
