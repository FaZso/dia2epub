#!/usr/bin/env bash
# Update dia2epub.py on an already-installed VPS.
# Usage (as root), from this directory:
#   bash update.sh
set -euo pipefail

APP_DIR=/opt/dia2epub
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/dia2epub.py" ]]; then
  echo "dia2epub.py missing next to update.sh" >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "$APP_DIR not found — run install.sh first." >&2
  exit 1
fi

install -m 644 -o dia2epub -g dia2epub "$SCRIPT_DIR/dia2epub.py" "$APP_DIR/dia2epub.py"
# optional dependency refresh
if [[ -x "$APP_DIR/venv/bin/pip" ]]; then
  sudo -u dia2epub "$APP_DIR/venv/bin/pip" install -q beautifulsoup4 certifi
fi

systemctl restart dia2epub
systemctl --no-pager --full status dia2epub || true
echo "Updated $APP_DIR/dia2epub.py and restarted dia2epub."
