# dia2epub

**Digitális Irodalmi Akadémia** (`reader.dia.hu`) HTML-publikációk → **EPUB 3**.

Személyes konverter: CLI és helyi webes felület, opcionális VPS-deploy (systemd + nginx + Basic Auth).

## Gyors start

```bash
pip install -r requirements.txt
python3 dia2epub.py --insecure "https://reader.dia.hu/document/Szerzo-Cim-12345" -o konyv.epub
# vagy csak ID:
python3 dia2epub.py --insecure 32493 -o konyv.epub
```

Webes UI:

```bash
python3 dia2epub.py --serve
# http://127.0.0.1:8765/
```

SSL hiba (macOS) esetén az `--insecure` flag vagy az automatikus újrapróbálás.

## Fő funkciók

- URL / slug / numerikus ID feloldás
- EPUB 3 navigáció: toc, page-list, landmarks (+ `toc.ncx`)
- Beágyazott képek, borító (ha elérhető)
- Batch mód (`--batch`)
- HTTP Basic Auth a web UI-hoz (`--auth` / `DIA2EPUB_AUTH`)

## Dokumentáció

| Fájl | Tartalom |
|------|----------|
| [PROJECT.md](PROJECT.md) | Cél, architektúra, API-váz, továbbfejlesztés AI-nak |
| [deploy/CLAUDE_DEPLOY.md](deploy/CLAUDE_DEPLOY.md) | VPS telepítés / frissítés |
| [HANDOFF.md](HANDOFF.md) | Átadás következő fejlesztőnek / AI-nak |

## Deploy (VPS)

```bash
cd deploy
DOMAIN=epub.example.com AUTH_USER=admin AUTH_PASS='…' bash install.sh
# később: bash update.sh
```

Részletek: `deploy/CLAUDE_DEPLOY.md`.

## Fejlesztés

```bash
python3 -m py_compile dia2epub.py
python3 test_dia2epub.py
```

Branch: `main`. A generált `.epub` fájlok nincsenek a repóban (`.gitignore`).

## Licenc / használat

Személyes eszköz a DIA nyilvános reader tartalmához. A forrásszövegek jogai a szerzőké / a DIA-é. A script nem hivatalos DIA API-kliens.
