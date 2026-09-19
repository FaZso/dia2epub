# Claude Code — dia2epub VPS deploy instructions

Deploy **exactly** as specified. Do not invent alternate layouts unless install fails.

## What this is

Personal web app: converts Digitális Irodalmi Akadémia (`reader.dia.hu`) HTML publications to EPUB 3.

- App binds **only** to `127.0.0.1:8765`
- Nginx (optional) terminates TLS and reverse-proxies
- HTTP Basic Auth via `DIA2EPUB_AUTH` in `/etc/dia2epub.env`

## Package contents (this folder)

| File | Role |
|------|------|
| `dia2epub.py` | Application (must sit next to scripts) |
| `install.sh` | First-time install |
| `update.sh` | Replace app code + restart |
| `dia2epub.service` | systemd unit |
| `dia2epub.env.example` | Auth env template |
| `nginx-dia2epub.conf` | Nginx site template |

## First-time install

```bash
cd /path/to/deploy
chmod +x install.sh update.sh
DOMAIN=YOUR_DOMAIN AUTH_USER=admin AUTH_PASS='STRONG_PASSWORD' bash install.sh
```

Without domain: `SKIP_NGINX=1 AUTH_USER=admin AUTH_PASS='...' bash install.sh`

## Update

```bash
bash update.sh
```

## Important constraints

- Do not bind to `0.0.0.0` without firewall + auth
- Do not remove Basic Auth in production
- Outbound HTTPS to `reader.dia.hu` required
- Do not open port 8765 on public firewall
- Do not commit real passwords
