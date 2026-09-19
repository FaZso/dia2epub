# dia2epub – fejlesztési összefoglaló

Személyes eszköz: **Digitális Irodalmi Akadémia** (`reader.dia.hu`) HTML-olvasó publikációiból **EPUB 3** fájl készítése.

Részletes dokumentáció a helyi fejlesztési összefoglalóból. Lásd még: [HANDOFF.md](HANDOFF.md), [README.md](README.md).

## Gyors start

```bash
pip install -r requirements.txt
python3 dia2epub.py --insecure 32493 -o konyv.epub
python3 dia2epub.py --serve
```

## Stack

Python 3.10+, urllib, beautifulsoup4, certifi, zipfile, http.server.

## Deploy

`deploy/CLAUDE_DEPLOY.md` — systemd + nginx + Basic Auth.

## Belépési pontok

`DIAClient`, `convert`, `write_epub`, `serve` / `DiaHandler`, `main`.

*Utolsó állapot: 2026-09-19. Repo: https://github.com/FaZso/dia2epub*
