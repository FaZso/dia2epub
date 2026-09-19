#!/usr/bin/env python3
"""dia2epub – gyors tesztkészlet (hálózat + egység)."""

from __future__ import annotations

import sys
import tempfile
import traceback
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dia2epub import (  # noqa: E402
    BASE,
    DIAClient,
    TokenError,
    convert,
    clean_xhtml,
)

SAMPLE_URL = (
    "https://reader.dia.hu/document/"
    "Czako_Gabor-A_szoba_Megvalto_Sarkanymese-32493"
)
SAMPLE_ID = "32493"


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def test_parse_ref() -> bool:
    print("1) parse_document_ref")
    c = DIAClient()
    cases = [
        (SAMPLE_ID, {"epub_id": "32493", "slug": None}),
        (SAMPLE_URL, {"epub_id": "32493", "slug": "Czako_Gabor-A_szoba_Megvalto_Sarkanymese-32493"}),
        ("/document/Foo_Bar-99", {"epub_id": "99", "slug": "Foo_Bar-99"}),
    ]
    good = True
    for ref, expect in cases:
        info = c.parse_document_ref(ref)
        for k, v in expect.items():
            if info.get(k) != v:
                fail(f"{ref!r}: {k}={info.get(k)!r} (várt {v!r})")
                good = False
    if good:
        ok("URL / ID / path felismerés")
    return good


def test_slug() -> bool:
    print("2) slug generálás")
    slugs = DIAClient._slug_candidates(
        "32493", "Czakó Gábor", "A szoba – Megváltó – Sárkánymese"
    )
    expected = "Czako_Gabor-A_szoba_Megvalto_Sarkanymese-32493"
    if expected in slugs and slugs[0] == expected:
        ok(f"első jelölt: {slugs[0]}")
        return True
    fail(f"slugok: {slugs}")
    return False


def test_clean_xhtml() -> bool:
    print("3) clean_xhtml")
    raw = b"""<!DOCTYPE html SYSTEM \"/x.dtd\">
<html><head><title>T</title>
<link rel=\"stylesheet\" href=\"/x.css\"/>
<script>x()</script></head>
<body><div class=\"cim\">I.</div>
<a name=\"DIAPage7\"></a>
<p>Sz\xc3\xb6veg.</p></body></html>"""
    xhtml, title, pages = clean_xhtml(raw, "fallback")
    checks = [
        (title == "T", f"title={title!r}"),
        (7 in pages, f"pages={pages}"),
        ("<script" not in xhtml.lower(), "script eltávolítva"),
        ("Szöveg." in xhtml or "Sz\u00f6veg." in xhtml, "szöveg megmaradt"),
        ('href="../Styles/style.css"' in xhtml, "epub css link"),
    ]
    good = True
    for cond, label in checks:
        if cond:
            ok(label)
        else:
            fail(label)
            good = False
    return good


def test_network_token() -> bool:
    print("4) hálózat: token (DIA)")
    c = DIAClient(timeout=25)
    try:
        tok = c.obtain_token(SAMPLE_ID)
        if not tok:
            fail("üres token")
            return False
        ok(f"token OK ({tok[:12]}…)")
        data = c.init_setting()
        n = len((data.get("view") or {}).get("components") or [])
        ok(f"init-setting: epubId={data.get('epubId')}, komponensek={n}")
        return n > 0
    except TokenError as e:
        fail(str(e))
        return False
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def test_mini_convert() -> bool:
    print("5) mini convert (első 2 komponens – struktúra)")
    c = DIAClient(timeout=20)
    try:
        c.obtain_token(SAMPLE_URL)
        data = c.init_setting()
        comps = (data.get("view") or {}).get("components") or []
        if not comps:
            fail("nincs komponens")
            return False
        raw = c.fetch_component(comps[0])
        xhtml, _, pages = clean_xhtml(raw)
        if len(xhtml) < 50:
            fail("üres xhtml")
            return False
        ok(f"komponens letöltve ({len(raw)} B, pages={pages[:3]}…)")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.epub"
            with zipfile.ZipFile(out, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                zf.writestr("META-INF/container.xml", "<container/>")
                zf.writestr("OEBPS/Text/a.xhtml", xhtml.encode("utf-8"))
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            if names[0] != "mimetype":
                fail("mimetype nem első")
                return False
            ok(f"epub zip smoke ({out.stat().st_size} B)")
        return True
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def test_web_ui(port: int = 8765) -> bool:
    print(f"6) webes UI :{port}")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            body = r.read()
            if r.status == 200 and b"DIA" in body:
                ok("GET / → 200")
                return True
            fail(f"status={r.status}")
    except Exception as e:
        fail(f"nem fut a szerver: {e}")
        print("     Indítsd: python3 dia2epub.py --serve")
    return False


def main() -> int:
    print("=== dia2epub tesztkörnyezet ===\n")
    results = [
        test_parse_ref(),
        test_slug(),
        test_clean_xhtml(),
        test_network_token(),
        test_mini_convert(),
        test_web_ui(),
    ]
    passed = sum(1 for x in results if x)
    print(f"\n=== {passed}/{len(results)} OK ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
