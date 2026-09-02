#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"
REQUESTED_VERSION="${1:-}"

SOURCE_VERSION="$(python3 - "$ROOT/src/immich_auto_archive.py" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^VERSION\s*=\s*["\x27]([^"\x27]+)["\x27]', text, re.MULTILINE)
if not match:
    raise SystemExit("Could not read VERSION from src/immich_auto_archive.py")
print(match.group(1))
PY
)"

VERSION="${REQUESTED_VERSION#v}"
if [[ -z "$VERSION" ]]; then
  VERSION="$SOURCE_VERSION"
fi

if [[ "$VERSION" != "$SOURCE_VERSION" ]]; then
  echo "ERROR: Requested version $VERSION does not match source VERSION=$SOURCE_VERSION" >&2
  exit 1
fi

rm -rf "$DIST"
mkdir -p "$DIST"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PKG_NAME="immich-auto-archive-$VERSION"
PKG_DIR="$WORK/$PKG_NAME"
mkdir -p "$PKG_DIR"

cp "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT/README.md" "$ROOT/LICENSE" "$PKG_DIR/"
cp -a "$ROOT/src" "$ROOT/systemd" "$PKG_DIR/"

tar -czf "$WORK/payload.tar.gz" -C "$WORK" "$PKG_NAME"

INSTALLER="$DIST/${PKG_NAME}-installer.sh"
cat > "$INSTALLER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

APP="immich-auto-archive"
VERSION="$VERSION"
PACKAGE="immich-auto-archive-$VERSION"

extract_payload() {
  local destination="\$1"
  local payload_line
  mkdir -p "\$destination"
  payload_line="\$(awk '/^__IMMICH_AUTO_ARCHIVE_PAYLOAD__\$/ { print NR + 1; exit }' "\$0")"
  if [[ -z "\$payload_line" ]]; then
    echo "ERROR: Installer payload marker not found." >&2
    exit 1
  fi
  tail -n +"\$payload_line" "\$0" | base64 --decode | tar -xzf - -C "\$destination"
}

if [[ "\${1:-}" == "--extract" ]]; then
  if [[ -z "\${2:-}" ]]; then
    echo "Usage: \$0 --extract DIRECTORY" >&2
    exit 2
  fi
  extract_payload "\$2"
  echo "Extracted \$PACKAGE to \$2"
  exit 0
fi

for cmd in base64 tar awk tail bash; do
  command -v "\$cmd" >/dev/null 2>&1 || {
    echo "ERROR: Required command missing: \$cmd" >&2
    exit 1
  }
done

TMP="\$(mktemp -d)"
trap 'rm -rf "\$TMP"' EXIT
extract_payload "\$TMP"
bash "\$TMP/\$PACKAGE/install.sh"
exit 0

__IMMICH_AUTO_ARCHIVE_PAYLOAD__
EOF

base64 "$WORK/payload.tar.gz" >> "$INSTALLER"
chmod 0755 "$INSTALLER"

echo "Built: $INSTALLER"
sha256sum "$INSTALLER"
