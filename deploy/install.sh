#!/usr/bin/env bash
# dia2epub VPS installer (Ubuntu/Debian)
# Usage (as root):
#   DOMAIN=epub.example.com AUTH_USER=admin AUTH_PASS='secret' bash install.sh
set -euo pipefail

APP_DIR=/opt/dia2epub
SERVICE_USER=dia2epub
DOMAIN="${DOMAIN:-}"
AUTH_USER="${AUTH_USER:-admin}"
AUTH_PASS="${AUTH_PASS:-}"
SKIP_NGINX="${SKIP_NGINX:-0}"
SKIP_CERTBOT="${SKIP_CERTBOT:-0}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$SCRIPT_DIR/dia2epub.py" ]]; then
  echo "dia2epub.py missing next to install.sh" >&2
  exit 1
fi

if [[ -z "$AUTH_PASS" ]]; then
  AUTH_PASS="$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-16)"
  echo "Generated AUTH_PASS: $AUTH_PASS"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx

if [[ "$SKIP_CERTBOT" != "1" && -n "$DOMAIN" ]]; then
  apt-get install -y certbot python3-certbot-nginx || true
fi

id -u "$SERVICE_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"

mkdir -p "$APP_DIR"
cp -f "$SCRIPT_DIR/dia2epub.py" "$APP_DIR/dia2epub.py"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

if [[ ! -d "$APP_DIR/venv" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
fi
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install beautifulsoup4 certifi

install -m 600 /dev/null /etc/dia2epub.env
cat > /etc/dia2epub.env <<EOF
DIA2EPUB_AUTH=${AUTH_USER}:${AUTH_PASS}
EOF
chown root:root /etc/dia2epub.env
chmod 600 /etc/dia2epub.env

install -m 644 "$SCRIPT_DIR/dia2epub.service" /etc/systemd/system/dia2epub.service
systemctl daemon-reload
systemctl enable dia2epub
systemctl restart dia2epub

if [[ "$SKIP_NGINX" != "1" ]]; then
  if [[ -z "$DOMAIN" ]]; then
    echo "DOMAIN not set — skipping nginx site. App listens on 127.0.0.1:8765 only."
  else
    conf=/etc/nginx/sites-available/dia2epub
    sed "s/YOUR_DOMAIN/${DOMAIN}/g" "$SCRIPT_DIR/nginx-dia2epub.conf" > "$conf"
    ln -sfn "$conf" /etc/nginx/sites-enabled/dia2epub
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl reload nginx

    if [[ "$SKIP_CERTBOT" != "1" ]]; then
      if command -v certbot >/dev/null 2>&1; then
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect || {
          echo "certbot failed — HTTP still works; run certbot manually later." >&2
        }
      fi
    fi
  fi
fi

systemctl --no-pager --full status dia2epub || true
echo
echo "=== Install complete ==="
echo "App:     systemctl status dia2epub"
echo "Auth:    user=${AUTH_USER}  pass=${AUTH_PASS}"
echo "Env:     /etc/dia2epub.env"
echo "Code:    $APP_DIR/dia2epub.py"
if [[ -n "$DOMAIN" ]]; then
  echo "URL:     https://${DOMAIN}/  (or http:// until certbot succeeds)"
else
  echo "Local:   curl -u ${AUTH_USER}:PASS http://127.0.0.1:8765/"
fi
echo "Update:  copy new dia2epub.py to $APP_DIR && systemctl restart dia2epub"
