# HANDOFF – dia2epub

**Célközönség:** következő fejlesztő vagy AI agent.  
**Utolsó frissítés:** 2026-09-19  
**Repo (helyi):** `/home/workdir/artifacts` · branch `main`  
**Tervezett remote:** `https://github.com/FaZso/dia2epub` (ha még nincs pusholva, lásd lent)

---

## 1. Mi ez?

Személyes **DIA → EPUB 3** konverter.

- Forrás: [reader.dia.hu](https://reader.dia.hu) REST olvasó API (nem hivatalos public SDK)
- Kimenet: érvényes EPUB 3 (toc + page-list + landmarks, plusz `toc.ncx`)
- Felületek: **CLI** és **helyi web UI** (`--serve`)
- Deploy: opcionális VPS (systemd + nginx + Basic Auth)

Kapcsolódó, de **nem** a fő kódbázis része: egyszeri **MEK** HTML→EPUB konverzió (Karácsony Benő: *Napos oldal*) – script nincs beolvasztva.

---

## 2. Hol van mi?

| Útvonal | Szerep |
|---------|--------|
| `dia2epub.py` | Egyetlen fő alkalmazás (~1280 sor): kliens, pipeline, EPUB írás, HTTP server |
| `test_dia2epub.py` | Smoke / unit jellegű tesztek |
| `requirements.txt` | `beautifulsoup4`, `certifi` |
| `PROJECT.md` | Részletes fejlesztési összefoglaló |
| `README.md` | Gyors start |
| `HANDOFF.md` | Ez a fájl |
| `deploy/` | VPS: `install.sh`, `update.sh`, unit, nginx, `CLAUDE_DEPLOY.md` |
| `.gitignore` | `*.epub`, venv, zip, secrets |

**Git:** igen, `main`. Remote: `https://github.com/FaZso/dia2epub`.  
**Nincs:** Docker, CI, `pyproject.toml`, hivatalos DIA dokumentáció.

---

## 3. Futtatás (30 mp)

```bash
cd /path/to/repo
pip install -r requirements.txt
python3 dia2epub.py --insecure 32493 -o konyv.epub
python3 dia2epub.py --serve          # http://127.0.0.1:8765/
python3 test_dia2epub.py
```

macOS SSL hiba → `--insecure` (vagy a kód automatikus fallbackje).

---

## 4. Architektúra (rövid)

```
URL/ID/slug
    → DIAClient.obtain_token()     # document URL 302 → token query
    → init_setting()               # components[], contents[] TOC
    → fetch_component() × N        # XHTML (+ ugyanígy a képek)
    → clean_xhtml / media rewrite
    → write_epub()                 # zip: mimetype, OPF, nav, Text, Images
```

**Belépési pontok a kódban:**

| Szimbólum | Feladat |
|-----------|---------|
| `DIAClient` | token, REST, component, metadata |
| `parse_document_ref` / `_slug_candidates` | bemenet normalizálás |
| `convert` | end-to-end |
| `write_epub` / `build_epub3_nav` | csomag + navigáció |
| `DiaHandler` / `serve` | web + opcionális Basic Auth |
| `main` | argparse |

Slug minta: `Szerzo_Nev-Cim-32493`. Numerikus ID → meta + slug tippek.

Képek: HTML `src="file….jpg"` → `GET /rest/epub-reader/component/{file}`.

---

## 5. Üzemeltetés (VPS)

1. Másold a `deploy/` mappát a szerverre (`dia2epub.py` mellette).
2. Root: `DOMAIN=… AUTH_USER=… AUTH_PASS=… bash install.sh`
3. Frissítés: új `dia2epub.py` → `bash update.sh`
4. App **csak** `127.0.0.1:8765`; publikus forgalom nginx 80/443 + auth.

Részletek: `deploy/CLAUDE_DEPLOY.md`.

Env: `/etc/dia2epub.env` → `DIA2EPUB_AUTH=user:pass`

---

## 6. Ismert csapdák (ne lépj bele újra)

1. **Token:** redirectet ne kövesd végig HTML-ig; a `token` a Location / final URL query-ben van.
2. **Unicode path:** komponens URL-t percent-encode-old (`Czakó` a fájlnévben).
3. **EPUB zip:** `mimetype` első bejegyzés, `ZIP_STORED`.
4. **Serve bind:** ne tedd `0.0.0.0`-ra auth és tűzfal nélkül.
5. **Sandbox ≠ user gép:** a fejlesztői 8765-ös port a user böngészőjéből nem látszik.
6. **DIA változhat:** reverse-engineered API; törés esetén init-setting / component útvonalakat ellenőrizd.

---

## 7. Mit csinálj következőnek (prioritás)

1. CI: `py_compile` + offline unit (mock HTTP).
2. Opcionális MEK/adapter modul – ne keverd a `DIAClient` token logikájába.
3. `requirements.txt` pin / `pyproject.toml`.
4. Cover / metadata edge case naplózás.

---

## 8. Handoff checklist az átvevő AI-nak

- [ ] Elolvastad: `HANDOFF.md`, `PROJECT.md`, `README.md`
- [ ] `python3 -m py_compile dia2epub.py` OK
- [ ] Egy élő konverzió (`--insecure` + ismert ID) OK
- [ ] `deploy/dia2epub.py` szinkronban van a gyökér `dia2epub.py`-val commit előtt
- [ ] Nem commitolsz `.epub`, `.env`, valódi jelszót
- [ ] Magyar UI/CLI üzeneteket megőrzöd, ha a user HU

---

## 9. Kapcsolat / ownership

- Tulajdonos GitHub: **FaZso**
- Fejlesztés története: chat + sandbox artifacts; deploy gyakran **Claude Code** a `CLAUDE_DEPLOY.md` szerint
- Ez a tool **nem** hivatalos DIA termék

---

*Ha valami ütközik a kóddal és ezzel a handoff-fal, a futó `dia2epub.py` az igazság.*
