#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dia2epub – Digitális Irodalmi Akadémia (reader.dia.hu) → EPUB 3

  pip3 install beautifulsoup4 certifi

  python3 dia2epub.py --serve
  python3 dia2epub.py "https://reader.dia.hu/document/Szerzo-Cim-12345" -o konyv.epub
  python3 dia2epub.py 32493 -o konyv.epub

SSL hiba esetén automatikusan újrapróbál --insecure módban.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

BASE = "https://reader.dia.hu"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class TokenError(RuntimeError):
    """DIA token / auth hiba."""


class NetworkError(RuntimeError):
    """Hálózati / timeout hiba."""


class ConvertError(RuntimeError):
    """Konverzió / tartalom hiba."""


def _ssl_context(insecure: bool = False) -> ssl.SSLContext:
    if insecure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _percent_encode_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/"),
            parts.query,
            parts.fragment,
        )
    )


class DIAClient:
    def __init__(self, timeout: float = 45.0, insecure: bool = False):
        self.timeout = timeout
        self.insecure = insecure
        self.token: str | None = None
        self._resolved_url: str | None = None
        self._ssl = _ssl_context(insecure)
        https = urllib.request.HTTPSHandler(context=self._ssl)
        self.opener = urllib.request.build_opener(
            https, urllib.request.HTTPCookieProcessor()
        )
        self.opener.addheaders = [("User-Agent", USER_AGENT)]

    def _request(self, url: str, method: str = "GET") -> tuple[int, bytes]:
        url = _percent_encode_url(url)
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", USER_AGENT)
        if self.token:
            req.add_header("Cookie", f"token={self.token}")
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            body = e.read() if e.fp else b""
            return e.code, body
        except urllib.error.URLError as e:
            raise NetworkError(f"Hálózati hiba: {e.reason}") from e
        except TimeoutError as e:
            raise NetworkError(f"Időtúllépés ({self.timeout}s): {url}") from e

    def fetch_metadata(self, epub_id: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"epubId": epub_id})
        status, body = self._request(f"{BASE}/rest/epub-reader/metadata?{q}")
        if status != 200:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return {}

    @staticmethod
    def parse_document_ref(ref: str) -> dict[str, str | None]:
        ref = ref.strip()
        epub_id = None
        slug = None
        document_url = None
        if ref.startswith("//"):
            ref = "https:" + ref
        if "reader.dia.hu" in ref or ref.startswith("http"):
            if not ref.startswith("http"):
                ref = "https://" + ref.lstrip("/")
            path = urllib.parse.urlparse(ref).path
            m = re.search(r"/document/([^/?#]+)", path)
            if m:
                slug = urllib.parse.unquote(m.group(1))
                document_url = f"{BASE}/document/{slug}"
        elif ref.startswith("/document/"):
            slug = urllib.parse.unquote(ref.split("/document/", 1)[1].split("?")[0])
            document_url = f"{BASE}/document/{slug}"
        elif re.fullmatch(r"\d+", ref):
            epub_id = ref
        else:
            slug = ref
            document_url = f"{BASE}/document/{slug}"
        if slug and not epub_id:
            m = re.search(r"-(\d+)$", slug)
            if m:
                epub_id = m.group(1)
        return {"raw": ref, "epub_id": epub_id, "slug": slug, "document_url": document_url}

    @staticmethod
    def _ascii_part(s: str) -> str:
        repl = {
            "á": "a", "é": "e", "í": "i", "ó": "o", "ö": "o", "ő": "o",
            "ú": "u", "ü": "u", "ű": "u",
            "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ö": "O", "Ő": "O",
            "Ú": "U", "Ü": "U", "Ű": "U",
        }
        for k, v in repl.items():
            s = s.replace(k, v)
        for cp in (0x2013, 0x2014, 0x2212, 0x2010, 0x2018, 0x2019, 0x201C, 0x201D):
            s = s.replace(chr(cp), " ")
        s = re.sub(r"[^A-Za-z0-9]+", "_", s)
        return re.sub(r"_+", "_", s).strip("_")

    @classmethod
    def _slug_candidates(cls, epub_id: str, author: str, title: str) -> list[str]:
        a, t = cls._ascii_part(author), cls._ascii_part(title)
        if not a or not t:
            return []
        cands = [f"{a}-{t}-{epub_id}", f"{a}-{t.replace('_', '-')}-{epub_id}", f"{a}_{t}-{epub_id}"]
        out, seen = [], set()
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def resolve_document_urls(self, ref: str) -> list[str]:
        info = self.parse_document_ref(ref)
        urls: list[str] = []
        if info["document_url"]:
            urls.append(info["document_url"])
        epub_id = info["epub_id"]
        if epub_id and not info["slug"]:
            meta = self.fetch_metadata(epub_id)
            md = meta.get("metaData") or {}
            author = (md.get("author") or "").strip()
            title = (md.get("bookTitle") or md.get("title") or "").strip()
            for slug in self._slug_candidates(epub_id, author, title):
                urls.append(f"{BASE}/document/{slug}")
            if not urls:
                urls.append(f"{BASE}/document/{epub_id}")
        out, seen = [], set()
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _token_from_url(self, url: str) -> str:
        class NoRedir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        last_err: Exception | None = None
        for attempt in range(3):
            https = urllib.request.HTTPSHandler(context=self._ssl)
            opener = urllib.request.build_opener(https, NoRedir)
            req = urllib.request.Request(_percent_encode_url(url), method="GET")
            req.add_header("User-Agent", USER_AGENT)
            try:
                with opener.open(req, timeout=self.timeout) as resp:
                    final = resp.geturl()
                    loc = resp.headers.get("Location")
                    if loc:
                        final = urllib.parse.urljoin(url, loc)
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location") if e.headers else None
                if e.code in (301, 302, 303, 307, 308) and loc:
                    final = urllib.parse.urljoin(url, loc)
                else:
                    raise TokenError(f"HTTP {e.code} ({url})") from e
            except Exception as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
                continue
            params = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
            token = params.get("token", [None])[0]
            if token:
                return urllib.parse.unquote(token)
            last_err = TokenError(f"Nincs token: {final}")
            break
        raise TokenError(f"{last_err} ({url})")

    def obtain_token(self, doc_id_or_url: str) -> str:
        candidates = self.resolve_document_urls(doc_id_or_url)
        if not candidates:
            raise TokenError(f"Érvénytelen hivatkozás: {doc_id_or_url!r}")
        last_err: Exception | None = None
        for url in candidates:
            try:
                self.token = self._token_from_url(url)
                self._resolved_url = url
                return self.token
            except TokenError as e:
                last_err = e
        raise TokenError(
            f"Nem sikerült token.\nUtolsó hiba: {last_err}\n"
            f"Próbált URL-ek:\n  - " + "\n  - ".join(candidates)
        )

    def init_setting(self) -> dict[str, Any]:
        if not self.token:
            raise TokenError("Nincs token.")
        status, body = self._request(f"{BASE}/rest/epub-reader/init-setting/")
        if status == 410:
            raise TokenError("Token lejárt (410).")
        if status != 200:
            raise TokenError(f"init-setting HTTP {status}: {body[:300]!r}")
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ConvertError(f"init-setting érvénytelen JSON: {e}") from e

    def fetch_component(self, path: str, retries: int = 3) -> bytes:
        if not path.startswith("http"):
            path = urllib.parse.urljoin(BASE, path)
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                status, body = self._request(path)
            except NetworkError as e:
                last_err = e
                time.sleep(0.6 * (attempt + 1))
                continue
            if status == 410:
                raise TokenError("Token lejárt (410). Indítsd újra a konverziót.")
            if status == 404:
                raise ConvertError(f"Komponens nem található (404): {path}")
            if status != 200:
                last_err = ConvertError(f"Komponens HTTP {status}: {path}")
                time.sleep(0.5 * (attempt + 1))
                continue
            if not body or len(body) < 20:
                last_err = ConvertError(f"Üres komponens: {path}")
                time.sleep(0.3)
                continue
            return body
        raise last_err or ConvertError(f"Komponens letöltés sikertelen: {path}")

    def fetch_cover(self, epub_id: str) -> tuple[bytes, str] | None:
        """Legjobb elérhető borítókép (JPEG/PNG). Vissza: (bytes, media_type) vagy None."""
        if not epub_id:
            return None
        candidates = [
            f"https://dia.hu/sites/default/files/covers/PIMDIA{epub_id}.jpg",
            f"https://dia.hu/sites/default/files/styles/large/public/covers/PIMDIA{epub_id}.jpg",
            f"https://dia.hu/sites/default/files/styles/medium/public/covers/PIMDIA{epub_id}.jpg",
            f"https://dia.hu/files/styles/large/public/covers/PIMDIA{epub_id}.jpg",
        ]
        best: tuple[bytes, str] | None = None
        best_len = 0
        for url in candidates:
            try:
                status, body = self._request(url)
            except Exception:
                continue
            if status != 200 or len(body) < 1000:
                continue
            if body[:3] == b"\xff\xd8\xff":
                mt = "image/jpeg"
            elif body[:8] == b"\x89PNG\r\n\x1a\n":
                mt = "image/png"
            elif body[:4] == b"RIFF" and b"WEBP" in body[:16]:
                mt = "image/webp"
            else:
                continue  # HTML hibaoldal
            if len(body) > best_len:
                best = (body, mt)
                best_len = len(body)
        return best


_PAGE_RE = re.compile(
    r"""<a\b[^>]*\b(?:name|id)=[\"']DIAPage(\d+)[\"'][^>]*/?>""",
    re.I,
)


_IMG_SRC_RE = re.compile(
    r"""(?is)(<img\b[^>]*?\bsrc\s*=\s*)([\"'])([^\"']+)\2"""
)
_LOCAL_MEDIA_RE = re.compile(
    r"""(?i)^(?!https?:|data:|mailto:|javascript:)([^?#/][^?#]*\.(?:jpe?g|png|gif|webp|svg))$"""
)


def extract_local_media_refs(html: str) -> list[str]:
    """Relative image filenames referenced in HTML (e.g. file0002804221.jpg)."""
    found: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"""(?is)\b(?:src|href)\s*=\s*[\"']([^\"']+)[\"']""", html
    ):
        ref = m.group(1).strip()
        if _LOCAL_MEDIA_RE.match(ref) and ref not in seen:
            seen.add(ref)
            found.append(ref)
    return found


def rewrite_media_srcs(html: str, media_map: dict[str, str]) -> str:
    """Rewrite local src=fileX.jpg -> ../Images/safe.jpg using media_map."""

    def repl(m: re.Match) -> str:
        prefix, quote, src = m.group(1), m.group(2), m.group(3).strip()
        if src in media_map:
            return f"{prefix}{quote}../Images/{media_map[src]}{quote}"
        # basename match
        base = src.rstrip("/").split("/")[-1]
        if base in media_map:
            return f"{prefix}{quote}../Images/{media_map[base]}{quote}"
        return m.group(0)

    return _IMG_SRC_RE.sub(repl, html)


def media_type_for(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")


def clean_xhtml(raw: bytes, fallback_title: str = "") -> tuple[str, str, list[int]]:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, count=1, flags=re.I)
    pages = sorted({int(x) for x in _PAGE_RE.findall(text)})
    title = fallback_title
    body_html = ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")
        t = soup.find("title")
        if t and t.get_text(strip=True):
            title = t.get_text(strip=True)
        for tag in soup.find_all(["script", "style", "link"]):
            tag.decompose()
        body = soup.find("body")
        if body is not None:
            for el in body.find_all(True):
                for a in list(el.attrs):
                    if str(a).lower().startswith("on"):
                        del el.attrs[a]
            body_html = body.decode_contents()
        else:
            body_html = str(soup)
    except Exception:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip() or title
        m = re.search(r"<body[^>]*>(.*)</body>", text, re.I | re.S)
        body_html = m.group(1) if m else text
        body_html = re.sub(r"<script\b[^>]*>.*?</script>", "", body_html, flags=re.I | re.S)
        body_html = re.sub(r"<link\b[^>]*/?>", "", body_html, flags=re.I)
        body_html = re.sub(r"<style\b[^>]*>.*?</style>", "", body_html, flags=re.I | re.S)
    title = (title or fallback_title or "...").strip()
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n<head>\n'
        '  <meta charset="utf-8"/>\n'
        f"  <title>{escape(title)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="../Styles/style.css"/>\n'
        "</head>\n"
        f"<body>\n{body_html}\n</body>\n</html>\n"
    )
    return xhtml, title, pages


def fix_internal_links(xhtml: str, id_map: dict | None = None) -> str:
    """javascript:void / data-jump -> valos belso EPUB hivatkozasok."""

    def repl_jump(m):
        full = m.group(0)
        dj = re.search(r'data-jump=[\"']([^\"']+)[\"']', full, re.I)
        if not dj:
            return full
        target = dj.group(1)
        if '#' in target:
            comp, anchor = target.split('#', 1)
        else:
            comp, anchor = target, ''
        fname = None
        if id_map:
            for src, fn in id_map.items():
                base = src.rstrip('/').split('/')[-1].replace('.xhtml', '')
                if comp == base or comp in src or comp in fn:
                    fname = fn
                    break
        if not fname:
            fname = re.sub(r'[^A-Za-z0-9._\-]+', '_', comp)
            if not fname.endswith('.xhtml'):
                fname += '.xhtml'
        href = ('%s#%s' % (fname, anchor)) if anchor else fname
        if re.search(r'\bhref=', full, re.I):
            full = re.sub(r'\bhref=[\"'][^\"']*[\"']', 'href="%s"' % href, full, count=1, flags=re.I)
        else:
            full = re.sub(r'<a\b', '<a href="%s"' % href, full, count=1, flags=re.I)
        return full

    xhtml = re.sub(r'<a\b[^>]*\bdata-jump=[^>]*>', repl_jump, xhtml, flags=re.I)

    def repl_void(m):
        tag = m.group(0)
        hm = re.search(r'\bhref=[\"']([^\"']*)[\"']', tag, re.I)
        if hm and not re.match(r'javascript:', hm.group(1), re.I) and hm.group(1) not in ('', '#'):
            return tag
        nm = re.search(r'\b(?:name|id)=[\"']([^\"']+)[\"']', tag, re.I)
        if nm:
            href = '#' + nm.group(1)
            if hm:
                return re.sub(r'\bhref=[\"'][^\"']*[\"']', 'href="%s"' % href, tag, count=1, flags=re.I)
            return re.sub(r'<a\b', '<a href="%s"' % href, tag, count=1, flags=re.I)
        for attr in ('data-target', 'data-href', 'data-note', 'data-ref'):
            am = re.search(r'\b' + attr + r'=[\"']([^\"']+)[\"']', tag, re.I)
            if am:
                val = am.group(1)
                href = val if val.startswith('#') or '.xhtml' in val else ('#' + val)
                if hm:
                    return re.sub(r'\bhref=[\"'][^\"']*[\"']', 'href="%s"' % href, tag, count=1, flags=re.I)
                return re.sub(r'<a\b', '<a href="%s"' % href, tag, count=1, flags=re.I)
        if hm:
            return re.sub(r'\bhref=[\"'][^\"']*[\"']', 'href="#"', tag, count=1, flags=re.I)
        return tag

    xhtml = re.sub(
        r'<a\b[^>]*href=[\"']\s*javascript:[^\"']*[\"'][^>]*>',
        repl_void,
        xhtml,
        flags=re.I,
    )
    return xhtml


DEFAULT_CSS = """\
body { font-family: serif; line-height: 1.5; margin: 1em; text-align: justify; }
h1, h2, h3, .cim, .cim1, .cim2, .fejezetcim {
  text-align: center; font-weight: bold; margin: 1.5em 0 1em; page-break-after: avoid;
}
p { margin: 0 0 0.6em; text-indent: 1.2em; }
p:first-child, .cim + p, h1 + p, h2 + p { text-indent: 0; }
.szerzo, .kiado, .ev { text-align: center; }
img { max-width: 100%; height: auto; }
.oldaltores { font-size: 0.7em; color: #888; vertical-align: super; }
"""


def slugify(s: str, max_len: int = 60) -> str:
    return DIAClient._ascii_part(s)[:max_len] or "konyv"


def _toc_ol(contents: list[dict], id_map: dict[str, str]) -> str:
    items = []
    for node in contents:
        src = node.get("src") or ""
        title = escape((node.get("title") or "...").strip() or "...")
        fname = id_map.get(src)
        children = node.get("children") or []
        if fname:
            inner = _toc_ol(children, id_map) if children else ""
            if inner:
                items.append(f'<li><a href="{fname}">{title}</a>\n<ol>\n{inner}</ol></li>')
            else:
                items.append(f'<li><a href="{fname}">{title}</a></li>')
        elif children:
            items.append(_toc_ol(children, id_map))
    return "\n".join(items)


def _first_body(contents: list[dict], id_map: dict[str, str]) -> str | None:
    for node in contents:
        src = node.get("src") or ""
        fname = id_map.get(src)
        if fname and "szerzoseg" not in src.lower():
            return fname
        found = _first_body(node.get("children") or [], id_map)
        if found:
            return found
    for node in contents:
        src = node.get("src") or ""
        if id_map.get(src):
            return id_map[src]
        found = _first_body(node.get("children") or [], id_map)
        if found:
            return found
    return None


def build_nav_points(contents: list[dict], id_map: dict[str, str]) -> str:
    parts: list[str] = []
    play = 0

    def walk(nodes: list[dict], depth: int) -> None:
        nonlocal play
        for node in nodes:
            src = node.get("src") or ""
            title = (node.get("title") or "...").strip() or "..."
            fname = id_map.get(src)
            children = node.get("children") or []
            if fname:
                play += 1
                ind = "  " * depth
                parts.append(
                    f'{ind}<navPoint id="np{play}" playOrder="{play}">\n'
                    f"{ind}  <navLabel><text>{escape(title)}</text></navLabel>\n"
                    f'{ind}  <content src="Text/{fname}"/>\n'
                )
                if children:
                    walk(children, depth + 1)
                parts.append(f"{ind}</navPoint>\n")
            elif children:
                walk(children, depth)

    walk(contents, 0)
    return "".join(parts)


def build_epub3_nav(contents, id_map, page_list):
    toc_ol = _toc_ol(contents, id_map)
    page_items = "\n".join(f'<li><a href="{escape(href)}">{n}</a></li>' for n, href in page_list)
    page_nav = ""
    if page_items:
        page_nav = f'''
<nav epub:type="page-list" id="page-list" hidden="hidden">
  <h1>Oldalak</h1>
  <ol>
{page_items}
  </ol>
</nav>
'''
    lm = ['<li><a epub:type="toc" href="#toc">Tartalom</a></li>']
    body = _first_body(contents, id_map)
    if body:
        lm.append(f'<li><a epub:type="bodymatter" href="{escape(body)}">Szoveg kezdete</a></li>')
    for node in contents:
        src = node.get("src") or ""
        fname = id_map.get(src)
        if fname and "szerzoseg" in src.lower():
            lm.append(f'<li><a epub:type="colophon" href="{escape(fname)}">Szerzosegi adatok</a></li>')
            break
    landmarks = f'''
<nav epub:type="landmarks" id="landmarks" hidden="hidden">
  <h1>Konyvjelzok</h1>
  <ol>
{chr(10).join("    " + x for x in lm)}
  </ol>
</nav>
'''
    return f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="hu" xml:lang="hu">
<head>
  <meta charset="utf-8"/>
  <title>Navigacio</title>
  <link rel="stylesheet" type="text/css" href="../Styles/style.css"/>
</head>
<body>
<nav epub:type="toc" id="toc">
  <h1>Tartalom</h1>
  <ol>
{toc_ol}
  </ol>
</nav>
{page_nav}
{landmarks}
</body>
</html>
'''


def write_epub(
    out_path,
    *,
    title,
    author,
    language,
    components,
    contents,
    xhtml_files,
    page_map,
    identifier,
    cover=None,
    images: dict[str, bytes] | None = None,
):
    """cover: optional (bytes, media_type).
    images: map original filename -> raw bytes (e.g. file0002804221.jpg).
    """
    id_map = {}
    manifest_items = []
    spine_items = []
    images = images or {}

    # safe image filenames + media_map for src rewrite
    media_map: dict[str, str] = {}
    image_items: list[tuple[str, str, bytes]] = []  # (safe, media_type, data)
    for i, (orig, data) in enumerate(sorted(images.items())):
        base = orig.rstrip("/").split("/")[-1]
        safe = re.sub(r"[^A-Za-z0-9._\-]+", "_", base) or f"img{i:04d}.bin"
        media_map[orig] = safe
        media_map[base] = safe
        mt = media_type_for(safe)
        image_items.append((safe, mt, data))
        manifest_items.append(
            '    <item id="img%04d" href="Images/%s" media-type="%s"/>' % (i, safe, mt)
        )

    # cover first in spine
    cover_ext = "jpg"
    cover_mt = "image/jpeg"
    if cover:
        cbytes, cover_mt = cover
        if cover_mt == "image/png":
            cover_ext = "png"
        elif cover_mt == "image/webp":
            cover_ext = "webp"
        manifest_items.append(
            '    <item id="cover-img" href="Images/cover.%s" media-type="%s" properties="cover-image"/>'
            % (cover_ext, cover_mt)
        )
        manifest_items.append(
            '    <item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>'
        )
        spine_items.append('    <itemref idref="cover"/>')

    for i, comp in enumerate(components):
        base = comp.rstrip("/").split("/")[-1]
        if not base.endswith((".xhtml", ".html", ".htm")):
            base += ".xhtml"
        safe = re.sub(r"[^A-Za-z0-9._\-]+", "_", base)
        id_map[comp] = safe
        iid = "c%04d" % i
        manifest_items.append(
            '    <item id="%s" href="Text/%s" media-type="application/xhtml+xml"/>' % (iid, safe)
        )
        spine_items.append('    <itemref idref="%s"/>' % iid)

    # fix links + image srcs now that maps are known
    fixed_xhtml = {}
    for comp, xhtml in xhtml_files.items():
        xhtml = fix_internal_links(xhtml, id_map)
        xhtml = rewrite_media_srcs(xhtml, media_map)
        fixed_xhtml[comp] = xhtml

    page_list = []
    seen = set()
    for comp in components:
        fname = id_map[comp]
        for n in page_map.get(comp, []):
            if n not in seen:
                seen.add(n)
                page_list.append((n, "%s#DIAPage%d" % (fname, n)))
    page_list.sort(key=lambda x: x[0])
    nav_xhtml = build_epub3_nav(contents, id_map, page_list)
    manifest_items = [
        '    <item id="css" href="Styles/style.css" media-type="text/css"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    ] + manifest_items
    modified = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta_cover = '    <meta name="cover" content="cover-img"/>\n' if cover else ""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" prefix="dcterms: http://purl.org/dc/terms/">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">%s</dc:identifier>
    <dc:title>%s</dc:title>
    <dc:creator>%s</dc:creator>
    <dc:language>%s</dc:language>
    <dc:publisher>Digitalis Irodalmi Akademia</dc:publisher>
    <meta property="dcterms:modified">%s</meta>
%s  </metadata>
  <manifest>
%s
  </manifest>
  <spine toc="ncx">
%s
  </spine>
</package>
""" % (
        escape(identifier),
        escape(title),
        escape(author),
        escape(language),
        modified,
        meta_cover,
        "\n".join(manifest_items),
        "\n".join(spine_items),
    )
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="%s"/>
    <meta name="dtb:depth" content="4"/>
    <meta name="dtb:totalPageCount" content="%d"/>
    <meta name="dtb:maxPageNumber" content="%d"/>
  </head>
  <docTitle><text>%s</text></docTitle>
  <navMap>
%s  </navMap>
</ncx>
""" % (
        escape(identifier),
        len(page_list),
        page_list[-1][0] if page_list else 0,
        escape(title),
        build_nav_points(contents, id_map),
    )
    cover_xhtml = ""
    if cover:
        cover_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <meta charset="utf-8"/>
  <title>Borito</title>
  <style>body{margin:0;text-align:center;} img{max-width:100%%;max-height:100vh;}</style>
</head>
<body>
  <img src="../Images/cover.%s" alt="Borito"/>
</body>
</html>
""" % cover_ext

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""")
        zf.writestr("OEBPS/content.opf", opf.encode("utf-8"))
        zf.writestr("OEBPS/toc.ncx", ncx.encode("utf-8"))
        zf.writestr("OEBPS/Styles/style.css", DEFAULT_CSS.encode("utf-8"))
        zf.writestr("OEBPS/Text/nav.xhtml", nav_xhtml.encode("utf-8"))
        if cover:
            zf.writestr("OEBPS/Images/cover.%s" % cover_ext, cover[0])
            zf.writestr("OEBPS/Text/cover.xhtml", cover_xhtml.encode("utf-8"))
        for safe, _mt, data in image_items:
            zf.writestr("OEBPS/Images/%s" % safe, data)
        for comp, xhtml in fixed_xhtml.items():
            zf.writestr("OEBPS/Text/%s" % id_map[comp], xhtml.encode("utf-8"))


def output_filename(author, title, date_str, epub_id=None, genre=""):
    """szerzo, cim, [mufaj,] kiadas_datuma.epub"""
    def clean(s):
        s = (s or "").strip()
        s = re.sub(r'[<>:"/\\|?*]', "", s)
        s = re.sub(r"\s+", " ", s).strip(" .,;")
        return s or "ismeretlen"

    parts = [clean(author), clean(title)]
    g = clean(genre)
    if g:
        # Capitalize first letter of genre for display
        g = g[0].upper() + g[1:] if g else g
        parts.append(g)
    d = clean(date_str) or (epub_id or "")
    if d:
        parts.append(d)
    name = ", ".join(parts) + ".epub"
    if len(name) > 180:
        name = name[:170] + ".epub"
    return name


def _safe_print(msg: str) -> None:
    try:
        print(msg, file=sys.stderr)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), file=sys.stderr)


def convert(doc, output=None, *, language="hu", delay=0.05, verbose=True, progress_cb=None, insecure=True):
    def log(msg):
        if verbose:
            _safe_print(msg)
        if progress_cb:
            progress_cb(msg)

    def try_client(ins):
        c = DIAClient(insecure=ins)
        log("Token kerese..." + (" (SSL nelkul)" if ins else ""))
        c.obtain_token(doc)
        return c

    try:
        client = try_client(insecure)
    except Exception as e:
        err = str(e)
        if not insecure and (
            "CERTIFICATE_VERIFY_FAILED" in err
            or "certificate verify failed" in err
            or "SSL" in err
        ):
            log("SSL hiba - ujraprobalas ellenorzes nelkul...")
            client = try_client(True)
        else:
            raise

    log("Konyvstruktura...")
    data = client.init_setting()
    epub_id = str(data.get("epubId") or "")
    view = data.get("view") or {}
    components = list(view.get("components") or [])
    contents = list(view.get("contents") or data.get("contents") or [])
    meta = data.get("metaData") or {}
    title = (meta.get("title") or meta.get("bookTitle") or "Nevtelen").strip()
    author = (meta.get("author") or "Ismeretlen").strip()
    publish_date = ""
    genre = ""

    extra = client.fetch_metadata(epub_id) if epub_id else {}
    md = extra.get("metaData") or {}
    if md.get("author"):
        author = md["author"].strip()
    # Clean title without genre suffix
    if meta.get("title"):
        title = meta["title"].strip()
    elif md.get("bookTitle"):
        title = md["bookTitle"].strip()
    publish_date = (md.get("publishDate") or md.get("sourcePublishDate") or "").strip()

    # Genre from mufaj attribute on content components
    for comp in components[:8]:
        try:
            raw0 = client.fetch_component(comp).decode("utf-8", "replace")
            m = re.search(r'\bmufaj=[\"']([^\"']+)[\"']', raw0, re.I)
            if m:
                genre = m.group(1).strip()
                break
        except Exception:
            pass
    if genre and title.lower().endswith(genre.lower()):
        title = title[: -len(genre)].strip(" ,.-")
    elif not genre:
        for g in (
            "Regény", "regény", "Verseskötet", "verseskötet",
            "Elbeszélések", "elbeszélések", "Dráma", "dráma",
            "Esszék", "esszék", "Tanulmányok", "tanulmányok",
            "Napló", "napló", "Publicisztika", "publicisztika",
            "Kritika", "kritika", "Színmű", "színmű",
        ):
            if title.endswith(g) and len(title) > len(g) + 2:
                title = title[: -len(g)].strip(" ,.-")
                genre = g
                break

    if not components:
        raise ConvertError(
            "Nincs letölthető komponens. "
            "A dokumentum üres, védett, vagy az ID/URL hibás."
        )

    log("Cim: %s" % title)
    log("Szerzo: %s" % author)
    log("Kiadás: %s" % (publish_date or "-"))
    if genre:
        log("Mufaj: %s" % genre)
    log("Komponensek: %d" % len(components))

    cover = None
    if epub_id:
        log("Borito letoltese...")
        cover = client.fetch_cover(epub_id)
        if cover:
            log("Borito: %d KB (%s)" % (len(cover[0]) // 1024, cover[1]))
        else:
            log("Borito nem talalhato")

    xhtml_files = {}
    page_map = {}
    media_names: list[str] = []
    media_seen: set[str] = set()
    failed = []
    for i, comp in enumerate(components, 1):
        name = comp.rstrip("/").split("/")[-1]
        log("  [%d/%d] %s" % (i, len(components), name))
        try:
            raw = client.fetch_component(comp)
            cleaned, _, pages = clean_xhtml(raw, fallback_title=title)
            xhtml_files[comp] = cleaned
            page_map[comp] = pages
            for ref in extract_local_media_refs(cleaned):
                if ref not in media_seen:
                    media_seen.add(ref)
                    media_names.append(ref)
        except TokenError:
            raise
        except Exception as e:
            failed.append((name, str(e)))
            log("  !!! hiba: %s — %s" % (name, e))
            # placeholder, hogy a spine ne törjön el
            xhtml_files[comp] = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Hiba</title></head>'
                '<body><p>[Hiányzó fejezet: %s]</p></body></html>\n'
            ) % escape(name)
            page_map[comp] = []
        if delay:
            time.sleep(delay)
    if failed and len(failed) == len(components):
        raise ConvertError(
            "Egyetlen fejezet sem töltődött le:\n  - "
            + "\n  - ".join("%s: %s" % (n, e) for n, e in failed)
        )
    if failed:
        log("Figyelem: %d fejezet hibás (placeholder került bele)." % len(failed))

    images: dict[str, bytes] = {}
    if media_names:
        log("Kepek: %d" % len(media_names))
        for i, ref in enumerate(media_names, 1):
            log("  [kep %d/%d] %s" % (i, len(media_names), ref))
            try:
                # Same REST endpoint as chapters: /rest/epub-reader/component/{file}
                path = ref if ref.startswith("/") else f"/rest/epub-reader/component/{ref}"
                blob = client.fetch_component(path)
                if blob and len(blob) > 32 and not blob.lstrip().startswith(b"{") and not blob.lstrip().startswith(b"<"):
                    images[ref] = blob
                else:
                    log("  !!! kep ures/hibas: %s (%d B)" % (ref, len(blob) if blob else 0))
            except Exception as e:
                log("  !!! kep hiba: %s — %s" % (ref, e))
            if delay:
                time.sleep(delay)
        log("Beagyazott kepek: %d" % len(images))

    nice = output_filename(author, title, publish_date, epub_id, genre=genre)
    if output is None:
        output = Path(nice)
    elif output.is_dir() or str(output).endswith(("/", "\\")):
        output = Path(output) / nice
    elif output.name.lower() in ("out.epub", "output.epub", "konyv.epub"):
        output = output.with_name(nice)

    write_epub(
        output,
        title=title,
        author=author,
        language=language,
        components=components,
        contents=contents,
        xhtml_files=xhtml_files,
        page_map=page_map,
        identifier=("dia:%s" % epub_id) if epub_id else ("dia:%s" % slugify(title)),
        cover=cover,
        images=images,
    )
    log("Kesz: %s" % output.resolve())
    return output


WEB_HTML = r"""<!DOCTYPE html>
<html lang="hu"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>DIA to EPUB</title>
<style>
:root{--bg:#0f1419;--card:#1a2332;--text:#e7ecf3;--muted:#8b9bb4;--accent:#3d8bfd;--ok:#4ade80;--danger:#f87171;--border:#2a3548}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;padding:1.5rem}
.card{width:min(560px,100%);background:var(--card);border:1px solid var(--border);border-radius:16px;padding:1.75rem 1.5rem}
h1{margin:0 0 .25rem;font-size:1.4rem}.sub{color:var(--muted);font-size:.9rem;margin-bottom:1.2rem}
label{display:block;font-size:.8rem;color:var(--muted);margin-bottom:.35rem}
input{width:100%;padding:.7rem .85rem;border-radius:10px;border:1px solid var(--border);background:#0d121a;color:var(--text);font-size:.95rem}
button{width:100%;margin-top:1rem;padding:.75rem;border:none;border-radius:10px;background:linear-gradient(135deg,var(--accent),#2563eb);color:#fff;font-weight:600;font-size:1rem;cursor:pointer}
button:disabled{opacity:.55}.log{margin-top:1rem;background:#0d121a;border:1px solid var(--border);border-radius:10px;padding:.75rem;max-height:200px;overflow:auto;font-family:monospace;font-size:.75rem;color:var(--muted);white-space:pre-wrap;display:none}
.log.active{display:block}.ok{color:var(--ok)}.err{color:var(--danger)}.note{margin-top:1rem;font-size:.75rem;color:var(--muted)}
</style></head><body>
<div class="card">
<h1>DIA → EPUB</h1>
<p class="sub">Digitalis Irodalmi Akademiai kiadvany → EPUB 3</p>
<label for="url">Dokumentum URL vagy ID (több sor = több könyv)</label>
<textarea id="url" rows="5" placeholder="https://reader.dia.hu/document/...&#10;32493&#10;33689" autocomplete="off" style="width:100%;padding:.7rem .85rem;border-radius:10px;border:1px solid var(--border);background:#0d121a;color:var(--text);font-size:.95rem;font-family:inherit;resize:vertical"></textarea>
<button id="go">EPUB készítése</button>
<div id="log" class="log"></div>
<p class="note">Személyes használatra. Több URL/ID: soronként egy. Webszerveren is futtatható.</p>
</div>
<script>
const logEl=document.getElementById("log");
function log(m,c){logEl.classList.add("active");const d=document.createElement("div");if(c)d.className=c;d.textContent=m;logEl.appendChild(d);logEl.scrollTop=logEl.scrollHeight}
function parseFilename(disp){
  if(!disp)return null;
  const star=/filename\*=UTF-8''([^;]+)/i.exec(disp);
  if(star){try{return decodeURIComponent(star[1]);}catch(e){}}
  const m=/filename="([^"]+)"/i.exec(disp)||/filename=([^;]+)/i.exec(disp);
  return m?m[1].trim():null;
}
async function convertOne(doc){
  const res=await fetch("/api/convert",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({document:doc})});
  if(!res.ok)throw new Error(await res.text());
  const name=parseFilename(res.headers.get("Content-Disposition"))||"konyv.epub";
  const blob=await res.blob();
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=name;a.click();URL.revokeObjectURL(a.href);
  return name;
}
document.getElementById("go").onclick=async()=>{
const raw=document.getElementById("url").value;
const items=raw.split(/[\r\n]+/).map(s=>s.trim()).filter(Boolean);
if(!items.length){log("Adj meg legalább egy URL-t vagy ID-t.","err");return}
const btn=document.getElementById("go");btn.disabled=true;logEl.innerHTML="";
log("Indítás: "+items.length+" könyv…");
let ok=0,fail=0;
for(const doc of items){
  log("→ "+doc);
  try{
    const name=await convertOne(doc);
    log("Kész: "+name,"ok");ok++;
  }catch(e){log("Hiba ("+doc+"): "+e.message,"err");fail++;}
}
log("Összesen: "+ok+" OK, "+fail+" hiba", fail?"err":"ok");
btn.disabled=false;
};
</script></body></html>
"""


class DiaHandler(BaseHTTPRequestHandler):
    server_version = "dia2epub/2.0"
    auth: tuple[str, str] | None = None  # set by serve()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _check_auth(self) -> bool:
        if not self.auth:
            return True
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(hdr[6:].strip()).decode("utf-8")
            user, _, pw = decoded.partition(":")
            return user == self.auth[0] and pw == self.auth[1]
        except Exception:
            return False

    def _require_auth(self) -> bool:
        if self._check_auth():
            return True
        body = b"Authentication required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="dia2epub"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _send(self, code, body, content_type, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._require_auth():
            return
        if self.path in ("/", "/index.html"):
            self._send(200, WEB_HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if not self._require_auth():
            return
        if self.path != "/api/convert":
            self._send(404, b"Not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
            doc = (payload.get("document") or "").strip()
            if not doc:
                raise ValueError("Hianyzo document mezo")
        except Exception as e:
            self._send(400, str(e).encode("utf-8"), "text/plain; charset=utf-8")
            return
        import tempfile
        from urllib.parse import quote
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = convert(doc, Path(tmp), delay=0.03, verbose=True, insecure=True)
                data = path.read_bytes()
                nice = path.name
                ascii_name = re.sub(r"[^A-Za-z0-9._\-]+", "_", nice).strip("._") or "konyv.epub"
                if not ascii_name.lower().endswith(".epub"):
                    ascii_name += ".epub"
                disp = (
                    f'attachment; filename="{ascii_name}"; '
                    f"filename*=UTF-8''{quote(nice)}"
                )
                self._send(200, data, "application/epub+zip", {
                    "Content-Disposition": disp,
                })
        except TokenError as e:
            self._send(502, f"Token hiba: {e}".encode("utf-8"), "text/plain; charset=utf-8")
        except NetworkError as e:
            self._send(503, f"Hálózati hiba: {e}".encode("utf-8"), "text/plain; charset=utf-8")
        except ConvertError as e:
            self._send(422, f"Konverzió hiba: {e}".encode("utf-8"), "text/plain; charset=utf-8")
        except Exception as e:
            self._send(500, f"Váratlan hiba: {type(e).__name__}: {e}".encode("utf-8"), "text/plain; charset=utf-8")


def serve(host="127.0.0.1", port=8765, auth: tuple[str, str] | None = None):
    DiaHandler.auth = auth
    try:
        httpd = ThreadingHTTPServer((host, port), DiaHandler)
    except OSError as e:
        print(
            f"Hiba: a {host}:{port} port nem elérhető ({e}).\n"
            f"Próbáld: python3 dia2epub.py --serve --port {port + 1}",
            file=sys.stderr,
        )
        raise SystemExit(4) from e
    print(f"DIA → EPUB webes felület: http://{host}:{port}/", file=sys.stderr)
    if auth:
        print(f"Basic Auth bekapcsolva (user={auth[0]})", file=sys.stderr)
    print("Állítsd le: Ctrl+C  |  Böngészőben nyisd meg a fenti címet.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nLeállítva.", file=sys.stderr)
    finally:
        httpd.server_close()


def main(argv=None):
    p = argparse.ArgumentParser(description="DIA -> EPUB 3")
    p.add_argument("document", nargs="*", help="reader.dia.hu URL vagy ID (több is megadható)")
    p.add_argument("--batch", type=Path, help="Listafájl: soronként egy URL/ID")
    p.add_argument("-o", "--output", type=Path, help="Kimeneti .epub")
    p.add_argument("-l", "--language", default="hu")
    p.add_argument("--delay", type=float, default=0.05)
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--insecure", action="store_true", default=True, help="SSL ellenorzes kikapcsolasa (alapbol be)")
    p.add_argument("--secure", action="store_false", dest="insecure", help="SSL ellenorzes bekapcsolasa")
    p.add_argument("--serve", action="store_true", help="Webes felulet")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--auth",
        metavar="USER:PASS",
        help="HTTP Basic Auth (vagy env: DIA2EPUB_AUTH=user:pass)",
    )
    args = p.parse_args(argv)
    if args.serve:
        auth = None
        raw = args.auth or os.environ.get("DIA2EPUB_AUTH") or ""
        if ":" in raw:
            u, _, pw = raw.partition(":")
            if u and pw:
                auth = (u, pw)
        serve(args.host, args.port, auth=auth)
        return 0

    docs: list[str] = list(args.document or [])
    if args.batch:
        try:
            lines = args.batch.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            print(f"Batch fájl hiba: {e}", file=sys.stderr)
            return 6
        for line in lines:
            line = re.sub(r"#.*$", "", line).strip()
            if line:
                docs.append(line)
    if not docs:
        p.print_help()
        return 1

    # több könyvnél -o csak egy fájlra értelmes → figyelmeztetés
    if args.output and len(docs) > 1:
        print("Figyelem: -o több könyvnél mellőzve (automatikus fájlnevek).", file=sys.stderr)
        args.output = None

    exit_code = 0
    for i, doc in enumerate(docs, 1):
        if len(docs) > 1:
            print(f"\n=== [{i}/{len(docs)}] {doc} ===", file=sys.stderr)
        try:
            convert(
                doc,
                args.output if len(docs) == 1 else None,
                language=args.language,
                delay=args.delay,
                verbose=not args.quiet,
                insecure=args.insecure,
            )
        except KeyboardInterrupt:
            print("\nMegszakítva.", file=sys.stderr)
            return 130
        except TokenError as e:
            print(f"Hiba (token): {e}", file=sys.stderr)
            exit_code = 2
        except NetworkError as e:
            print(f"Hálózati hiba: {e}", file=sys.stderr)
            exit_code = 3
        except ConvertError as e:
            print(f"Konverzió hiba: {e}", file=sys.stderr)
            exit_code = 5
        except urllib.error.URLError as e:
            print(f"Hálózati hiba: {e}", file=sys.stderr)
            exit_code = 3
        except OSError as e:
            print(f"Fájl hiba: {e}", file=sys.stderr)
            exit_code = 6
        except Exception as e:
            print(f"Váratlan hiba ({type(e).__name__}): {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
