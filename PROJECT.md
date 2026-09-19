# dia2epub – fejlesztési összefoglaló

Személyes eszköz: **Digitális Irodalmi Akadémia** (`reader.dia.hu`) HTML-olvasó publikációiból **EPUB 3** fájl készítése. Később ad hoc **MEK** (mek.oszk.hu) HTML-kötetek is EPUB-ba kerültek, de a karbantartott termék a DIA-konverter.

---

## 1. Cél

| Cél | Részlet |
|-----|---------|
| Fő funkció | DIA reader URL / slug / numerikus ID → érvényes EPUB 3 |
| Navigáció | EPUB 3 `nav.xhtml`: **toc**, **page-list**, **landmarks** + EPUB 2 kompatibilis `toc.ncx` |
| Tartalom | Fejezet XHTML-ek, beágyazott képek, borító (ha elérhető), belső linkek, lábjegyzetek ahol a forrás támogatja |
| Használat | CLI + helyi webes UI (`--serve`); VPS-en nginx + systemd + Basic Auth |
| Nem cél | Általános web-scraper, más kiadók API-ja (MEK egyedi script volt, nincs beépítve a `dia2epub.py`-ba) |

Példa bemenetek:

```text
https://reader.dia.hu/document/Czako_Gabor-A_szoba_Megvalto_Sarkanymese-32493
32493
Czako_Gabor-A_szoba_Megvalto_Sarkanymese-32493
```

---

## 2. Eszközök és stack

| Réteg | Technológia |
|-------|-------------|
| Nyelv | Python 3.10+ |
| HTTP | `urllib.request` (cookie + token a 302 Location-ből) |
| HTML | BeautifulSoup4 |
| EPUB | `zipfile` (mimetype, container, OPF, NCX, nav.xhtml) |
| Web UI | beépített `ThreadingHTTPServer` |
| Deploy | systemd unit + nginx reverse proxy + Basic Auth |

Függőségek: `beautifulsoup4`, `certifi` (opcionális, SSL-hez).

---

## 3. Fő fájlok

| Fájl | Szerep |
|------|--------|
| `dia2epub.py` | Teljes konverter + web UI |
| `test_dia2epub.py` | Unit / smoke tesztek |
| `requirements.txt` | pip függőségek |
| `deploy/` | install.sh, update.sh, service, nginx conf |
| `HANDOFF.md` | Átadás következő AI / fejlesztőnek |

---

## 4. Működés vázlat

1. **Token**: dokumentum URL → 302 Location → `token=` query param → Cookie.
2. **init-setting**: JSON (components, contents, metaData).
3. **Komponensek**: `/rest/epub-reader/component/...` letöltés.
4. **Tisztítás**: script/style eltávolítás, XHTML, DIAPage anchorok, képek.
5. **EPUB csomagolás**: OPF + nav (toc / page-list / landmarks) + NCX + képek + borító.

---

## 5. Ismert korlátok / tippek

- SSL: macOS-en gyakran kell `--insecure` (vagy automatikus fallback).
- Nem-ASCII Content-Disposition: ASCII + `filename*=UTF-8''`.
- Duplikált fejezetcímek: a forrás HTML-ből jönnek; tisztítás a clean lépésben.
- Lábjegyzetek: a forrás `bN` / `nN` anchorjaira épül (ahol vannak).

---

## 6. Továbbfejlesztés (AI handoff)

Lásd **HANDOFF.md**. Röviden: stabil a DIA útvonal; MEK külön ad-hoc; deploy script kész; GitHub: `FaZso/dia2epub`.
