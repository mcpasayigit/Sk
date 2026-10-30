#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bir WordPress sitesinin ziyaretçiye görünen halini (sayfalar, CSS, JS, görseller,
fontlar, PDF/DOCX gibi dokümanlar) statik dosyalar olarak indirir.

 - Dosyalar sunucudan geldiği gibi, bayt bayt aynı kaydedilir (içerik değiştirilmez).
 - PHP, wp-admin, REST API, feed gibi dinamik uçlar kopyalanmaz.
 - Sadece Python standart kütüphanesini kullanır.

Ortam değişkenleri:
  SITE_URL        kopyalanacak adres               (varsayılan https://suleymankaynak.com)
  OUT_DIR         dosyaların yazılacağı klasör     (varsayılan site)
  REPORT_DIR      raporların yazılacağı klasör     (varsayılan report)
  WORKERS         paralel indirme sayısı           (varsayılan 4)
  DELAY           her istek öncesi bekleme, sn     (varsayılan 0.15)
  TIMEOUT         istek zaman aşımı, sn            (varsayılan 45)
  MAX_FILE_BYTES  tek dosya üst sınırı             (varsayılan 95 MB; GitHub sınırı 100 MB)
  MAX_URLS        tarama üst sınırı                (varsayılan 20000)
  EXPECT_IP       sitenin çözümlenmesi beklenen IP (varsa rapora karşılaştırma eklenir)
"""
import gzip
import hashlib
import html
import http.cookiejar
import json
import os
import posixpath
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from html.parser import HTMLParser

# --------------------------------------------------------------------------- ayarlar
SITE = os.environ.get("SITE_URL", "https://suleymankaynak.com").rstrip("/")
OUT = os.path.abspath(os.environ.get("OUT_DIR", "site"))
REPORT = os.path.abspath(os.environ.get("REPORT_DIR", "report"))
WORKERS = int(os.environ.get("WORKERS", "4"))
DELAY = float(os.environ.get("DELAY", "0.15"))
TIMEOUT = int(os.environ.get("TIMEOUT", "45"))
MAX_FILE = int(os.environ.get("MAX_FILE_BYTES", str(95 * 1024 * 1024)))
MAX_URLS = int(os.environ.get("MAX_URLS", "20000"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "3000"))          # HTML sayfa üst sınırı (sonsuz takvim vb. için)
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "0"))         # 0 = sınırsız; dolunca yeni adres alınmaz, rapor yazılır
PROGRESS_PUSH = os.environ.get("PROGRESS_PUSH", "") == "1"    # ilerlemeyi 'progress' dalına gönder
PROGRESS_EVERY = int(os.environ.get("PROGRESS_EVERY", "60"))
EXPECT_IP = os.environ.get("EXPECT_IP", "").strip()
PARSE_MAX = 8 * 1024 * 1024

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

_p = urllib.parse.urlsplit(SITE)
NETLOC = _p.netloc.lower()
HOST = (_p.hostname or "").lower()
APEX = HOST[4:] if HOST.startswith("www.") else HOST
HOSTS = {APEX, "www." + APEX}
HP = NETLOC[4:] if NETLOC.startswith("www.") else NETLOC      # port dahil
BASE = "%s://%s" % (_p.scheme, NETLOC)

ASSET_EXT = set("""css js mjs json xml xsl xslt txt map
png jpg jpeg gif webp avif svg ico bmp tif tiff heic
woff woff2 ttf otf eot
mp4 m4v webm ogv mov avi mp3 m4a ogg wav
pdf doc docx xls xlsx ppt pptx odt ods csv rtf zip rar 7z gz""".split())
IMAGE_EXT = set("png jpg jpeg gif webp avif svg ico bmp tif tiff heic".split())
HTML_TYPES = {"text/html", "application/xhtml+xml"}

SKIP_RE = re.compile(
    r"(?:^/(?:wp-admin|wp-json|wp-login|xmlrpc|wp-cron|wp-comments-post|cdn-cgi)(?:[./]|$))"
    r"|(?:/(?:feed|trackback|embed)/?$)|(?:/comments/feed/?$)|(?:\.php(?:/|$))", re.I)

OPTIONAL = {BASE + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/favicon.ico",
                               "/wp-sitemap.xsl", "/robots.txt")}

SAFE = "/:@!$&'()*+,;=-._~"
ERR = "surrogateescape"

# --------------------------------------------------------------------------- durum
lock = threading.Lock()
seen = set()                    # kuyruğa alınmış anahtarlar
results = {}                    # anahtar -> {file, bytes, sha256, type}
alias = {}                      # istenen -> yönlendirme sonrası anahtar
failed = {}                     # anahtar -> sebep
skipped = defaultdict(set)      # sebep -> {url}
refs = defaultdict(set)         # kaynak dosya -> {aday url}
ext_hosts = Counter()           # (etiket, host) -> sayı
written = {}                    # dosya yolu -> sha256
stub_files = set()              # yönlendirme için bizim ürettiğimiz sayfalar
rest_known = set()              # REST API'nin listelediği adresler
collisions = []
oversize = {}
flags = {}
empty = {}                      # 0 baytlık gelen dosyalar -> başlıklar
stats = {"bytes": 0, "pages": 0}
status_counter = Counter()      # HTTP durum kodu / hata türü -> sayı
recent = deque(maxlen=12)       # son kaydedilenler (ilerleme raporu için)
qstate = {"queue": 0, "inflight": 0, "done": 0}
T0 = time.time()


def unq(s):
    return urllib.parse.unquote(s, errors=ERR)


# --------------------------------------------------------------------------- adres işlemleri
def _dots(path):
    """'/a/b/../c' -> '/a/c' (RFC 3986 nokta parçalarını çözer; tarayıcıların yaptığı gibi)."""
    if "." not in path:
        return path
    parts = path.split("/")
    out = []
    for seg in parts:
        if seg == "..":
            if len(out) > 1:
                out.pop()
        elif seg != ".":
            out.append(seg)
    if parts[-1] in (".", ".."):
        out.append("")
    res = "/".join(out)
    return res if res.startswith("/") else "/" + res


def _admin_asset(path):
    """wp-admin altındaki css/js/görsel gibi statik dosyalar sayfalarca kullanılabilir; php ve sayfalar alınmaz."""
    low = path.lower()
    if not low.startswith("/wp-admin/") or re.search(r"\.php(?:/|$)", low) or low.endswith("/"):
        return False
    ext = posixpath.splitext(low)[1].lstrip(".")
    return ext in ASSET_EXT and ext not in ("xml", "json", "txt")


def canon(url, base=None):
    """Site içi adresi normalize eder (aynı host/şema, tutarlı kodlama). Site dışıysa None."""
    try:
        if base:
            url = urllib.parse.urljoin(base, url)
        s = urllib.parse.urlsplit(url.strip())
        if s.scheme not in ("http", "https") or (s.hostname or "").lower() not in HOSTS:
            return None
        path = urllib.parse.quote(_dots(unq(s.path or "/")), safe=SAFE, errors=ERR)
        return BASE + path + ("?" + s.query if s.query else "")
    except Exception:
        return None


def plan(c):
    """Normalize adres -> ((anahtar, indirilecek_url, dosya_mı), None) ya da (None, atlama_sebebi)."""
    s = urllib.parse.urlsplit(c)
    path = unq(s.path)
    if SKIP_RE.search(path) and not _admin_asset(path):
        return None, "dinamik uç (php / wp-admin / feed / api)"
    if any(ch in path for ch in "*{}<>|^`\\"):
        return None, "şablon/desen (gerçek adres değil)"
    if path.startswith(("/wp-content/", "/wp-includes/")) and path.endswith("/"):
        return None, "klasör adresi (dosya değil)"
    segs = [x for x in path.split("/") if x]
    if any(x in (".", "..") for x in segs):
        return None, "geçersiz yol"
    if any(x.startswith(".") and x != ".well-known" for x in segs):
        return None, "gizli dosya"
    ext = posixpath.splitext(path)[1].lower().lstrip(".")
    asset = ext in ASSET_EXT
    if s.query and not asset:
        return None, "sorgu parametreli sayfa"
    return (BASE + s.path, c, asset), None


def shorten(part):
    raw = part.encode("utf-8", ERR)
    if len(raw) <= 200:
        return part
    ext = posixpath.splitext(part)[1][:12]
    return hashlib.sha1(raw).hexdigest()[:20] + ext


def rel_for(key, is_html):
    p = unq(urllib.parse.urlsplit(key).path).lstrip("/")
    if p == "" or p.endswith("/"):
        p += "index.html"
    elif is_html and posixpath.splitext(p)[1].lower() not in (".html", ".htm"):
        p += "/index.html"
    return "/".join(shorten(x) for x in p.split("/"))


def stub(fkey):
    path = urllib.parse.urlsplit(fkey).path or "/"
    e = html.escape(path)
    return ('<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Yönlendiriliyor</title>'
            '<link rel="canonical" href="%s"><meta http-equiv="refresh" content="0; url=%s">'
            '<script>location.replace(%s)</script></head><body><a href="%s">%s</a></body></html>\n'
            % (e, e, json.dumps(path), e, e)).encode("utf-8")


# --------------------------------------------------------------------------- HTTP
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPCookieProcessor(_jar))


def _read(resp, limit):
    cl = resp.headers.get("Content-Length")
    if cl and cl.isdigit() and int(cl) > limit:
        return None, int(cl)
    chunks, total = [], 0
    while True:
        b = resp.read(1 << 16)
        if not b:
            break
        total += len(b)
        if total > limit:
            return None, total
        chunks.append(b)
    return b"".join(chunks), total


def _fetch_once(url):
    last = "bilinmeyen hata"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                "Accept-Encoding": "identity",
                "Referer": BASE + "/",
            })
            try:
                resp = _opener.open(req, timeout=TIMEOUT)
            except urllib.error.HTTPError as e:
                resp = e
            status = resp.getcode()
            out = {"status": status, "headers": resp.headers, "body": b"", "error": None,
                   "ctype": (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()}
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                last = "HTTP %s" % status
                time.sleep(2 * (attempt + 1))
                continue
            if status == 200:
                body, total = _read(resp, MAX_FILE)
                if body is None:
                    out["toolarge"] = total
                    return out
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and not enc and total != int(cl):
                    # bağlantı yarıda kesildi: eksik dosya kaydetme, yeniden dene
                    last = "kesik indirme (beklenen %s bayt, gelen %d)" % (cl, total)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if enc == "gzip":
                    body = gzip.decompress(body)
                elif enc == "deflate":
                    try:
                        body = zlib.decompress(body)
                    except zlib.error:
                        body = zlib.decompress(body, -15)
                out["body"] = body
            return out
        except Exception as e:  # ağ hatası, zaman aşımı, SSL...
            last = "%s: %s" % (type(e).__name__, str(e)[:120])
            time.sleep(1.5 * (attempt + 1))
    return {"status": 0, "headers": {}, "body": b"", "error": last, "ctype": ""}


def fetch(url):
    """Yönlendirmeleri elle takip eder (site dışına çıkarsa durur)."""
    chain, cur = [], url
    for _ in range(8):
        r = _fetch_once(cur)
        if r["error"] or r["status"] not in (301, 302, 303, 307, 308):
            r["chain"], r["final"] = chain, cur
            return r
        loc = r["headers"].get("Location") if r["headers"] else None
        if not loc:
            r.update(chain=chain, final=cur, error="Location başlığı yok")
            return r
        nxt = urllib.parse.urljoin(cur, loc)
        chain.append((r["status"], nxt))
        if canon(nxt) is None:
            r.update(chain=chain, final=nxt, error="site dışına yönlendirme: " + nxt[:90])
            return r
        cur = nxt      # sunucu www/şema zorluyorsa aynen takip et (döngüye girmesin)
    return {"status": 0, "headers": {}, "body": b"", "ctype": "", "chain": chain, "final": cur,
            "error": "çok fazla yönlendirme"}


# --------------------------------------------------------------------------- ayrıştırma
_U_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
ABS_RE = re.compile(
    r"(?:https?:)?(?:\\?/){2}(?:www\.)?" + re.escape(HP) +
    r"(?P<path>(?:\\?/[^\s\"'<>()\\,;{}|^`\[\]]*)+)", re.I)
ROOT_RE = re.compile(
    r"""(?<=["'(=\s,])(?:\\?/)wp-(?:content|includes)(?:\\?/[^\s"'<>()\\,;{}|^`\[\]]*)+""")
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s"']+))\s*\)""", re.I)
_CSS_IMPORT = re.compile(r"""@import\s+(?:"([^"]+)"|'([^']+)')""", re.I)

URL_ATTRS = {"href", "src", "data-src", "data-lazy-src", "data-original", "data-bg", "data-background",
             "data-poster", "poster", "data", "data-href", "data-url", "data-image", "data-thumb",
             "data-large_image", "data-full", "action", "background", "longdesc", "manifest"}
EXT_TAGS = {"script", "link", "iframe", "img", "source", "video", "audio", "embed", "object",
            "form", "track", "input", "style"}


def css_refs(text, base):
    found = []
    for m in _CSS_URL.finditer(text):
        found.append(next((g for g in m.groups() if g), ""))
    for m in _CSS_IMPORT.finditer(text):
        found.append(next((g for g in m.groups() if g), ""))
    res = []
    for u in found:
        u = u.strip()
        if not u or u.startswith(("data:", "#", "about:", "blob:")):
            continue
        try:
            res.append(urllib.parse.urljoin(base, u))
        except ValueError:
            pass
    return res


class AttrParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.urls, self.ext, self._style = base, [], [], None

    def _add(self, tag, v):
        v = (v or "").strip()
        if not v or v.startswith(("#", "data:", "javascript:", "mailto:", "tel:", "about:", "blob:")):
            return
        try:
            u = urllib.parse.urljoin(self.base, v)
            c = canon(u)
            if c:
                self.urls.append(c)
            elif tag in EXT_TAGS and u.startswith(("http://", "https://")):
                self.ext.append((tag, (urllib.parse.urlsplit(u).hostname or "").lower()))
        except ValueError:
            pass

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href"):
            try:
                self.base = urllib.parse.urljoin(self.base, a["href"])
            except ValueError:
                pass
        for k, v in a.items():
            if k in URL_ATTRS:
                self._add(tag, v)
            elif k.endswith("srcset"):
                for part in v.split(","):
                    tok = part.strip().split()
                    if tok:
                        self._add(tag, tok[0])
            elif k == "style":
                for u in css_refs(v, self.base):
                    self._add(tag, u)
        if tag == "style":
            self._style = []

    def handle_endtag(self, tag):
        if tag == "style" and self._style is not None:
            for u in css_refs("".join(self._style), self.base):
                self._add("style", u)
            self._style = None

    def handle_data(self, data):
        if self._style is not None:
            self._style.append(data)


def _clean(p):
    return p.replace("\\/", "/").rstrip(".,;:!'\"\\")


def extract(text, base, kind):
    """Metindeki site içi adresleri bulur. -> ([normalize url], [(etiket, dış host)])"""
    urls, exts, scan = [], [], text
    if kind == "html":
        scan = html.unescape(text)
        p = AttrParser(base)
        try:
            p.feed(text)
            p.close()
        except Exception:
            pass
        urls += p.urls
        exts += p.ext
    elif kind == "css":
        urls += css_refs(text, base)
    scan = _U_RE.sub(lambda m: chr(int(m.group(1), 16)), scan)
    for m in ABS_RE.finditer(scan):
        urls.append(BASE + _clean(m.group("path")))
    for m in ROOT_RE.finditer(scan):
        urls.append(BASE + _clean(m.group(0)))
    out = []
    for u in urls:
        c = canon(u)
        if c:
            out.append(c)
    return list(dict.fromkeys(out)), exts


def text_kind(ctype, ext):
    if ctype in HTML_TYPES:
        return "html"
    if ctype == "text/css" or ext == "css":
        return "css"
    if ext in ("js", "mjs", "json", "xml", "xsl", "xslt", "txt", "svg") or ctype.startswith("text/") \
            or ctype.endswith(("javascript", "json", "xml")):
        return "text"
    return None


# --------------------------------------------------------------------------- tarama
def register(c, src):
    task, why = plan(c)
    with lock:
        if src is not None:
            refs[src].add(c)
        if task is None:
            skipped[why].add(c)
            return None
        key = task[0]
        if key in seen:
            return None
        if len(seen) >= MAX_URLS:
            flags["MAX_URLS sınırına ulaşıldı"] = True
            return None
        if not task[2]:                      # dosya değil, sayfa
            if stats["pages"] >= MAX_PAGES:
                flags["MAX_PAGES sınırına ulaşıldı (yeni sayfalar alınmadı)"] = True
                return None
            stats["pages"] += 1
        seen.add(key)
    return task


def fail(key, why):
    with lock:
        failed[key] = why
    return []


def write(rel, data, key):
    full = os.path.abspath(os.path.join(OUT, rel))
    if not full.startswith(OUT + os.sep):
        raise ValueError("klasör dışına çıkan yol: " + rel)
    sha = hashlib.sha256(data).hexdigest()
    with lock:
        prev = written.get(rel)
        if prev is not None:
            if prev != sha:
                collisions.append((rel, key))
            return
        written[rel] = sha
    tmp = full + ".indiriliyor"
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, full)                # yarım dosya kalmasın
        with lock:
            stats["bytes"] += len(data)
    except OSError:
        with lock:
            written.pop(rel, None)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def handle(task):
    key, fetch_url, _asset = task
    if DELAY:
        time.sleep(DELAY)
    t_start = time.time()
    r = fetch(fetch_url)
    with lock:
        status_counter[r["status"] if not r.get("error") else "hata: " + r["error"][:40]] += 1
    if r.get("error"):
        return fail(key, r["error"])
    if r["status"] != 200:
        return fail(key, "HTTP %s" % r["status"])
    if not r.get("toolarge") and not r["body"] and r["ctype"] not in HTML_TYPES:
        for _ in range(2):                   # boş geldiyse (geçici olabilir) iki kez daha dene
            time.sleep(1.5)
            r2 = fetch(fetch_url)
            if not r2.get("error") and r2["status"] == 200 and r2["body"]:
                r = r2
                break
    if r.get("toolarge"):
        with lock:
            oversize[key] = r["toolarge"]
        return fail(key, "çok büyük (%s), atlandı" % mb(r["toolarge"]))
    final_c = canon(r["final"]) or fetch_url
    fplan, why = plan(final_c)
    if fplan is None:
        return fail(key, "yönlendirme hedefi kopyalanmıyor: " + why)
    fkey = fplan[0]
    ctype = r["ctype"]
    is_html = ctype in HTML_TYPES
    ext = posixpath.splitext(unq(urllib.parse.urlsplit(fkey).path))[1].lower().lstrip(".")
    if is_html and ext in IMAGE_EXT:
        return fail(key, "sahte 200: dosya yerine HTML sayfası döndü")
    body = r["body"]
    sha = hashlib.sha256(body).hexdigest()
    rel_final, rel_orig = rel_for(fkey, is_html), rel_for(key, is_html)
    owner = True
    if fkey != key:
        with lock:
            owner = fkey not in seen
            seen.add(fkey)
            alias[key] = fkey
    try:
        if owner:
            write(rel_final, body, fkey)
            with lock:
                results[fkey] = dict(file=rel_final, bytes=len(body), sha256=sha, type=ctype)
                recent.append((fkey[-90:], len(body), round(time.time() - t_start, 1)))
                if not body:
                    hd = r.get("headers") or {}
                    empty[fkey] = {k: hd.get(k) for k in ("Content-Length", "Content-Type", "Last-Modified")
                                   if hd.get(k)}
        if rel_orig != rel_final:
            write(rel_orig, stub(fkey) if is_html else body, key)
            with lock:
                if is_html:
                    stub_files.add(rel_orig)
                results[key] = dict(file=rel_orig, bytes=len(body), sha256=sha, type=ctype, alias_of=fkey)
    except (OSError, ValueError) as e:
        return fail(key, "dosya yazılamadı: %s" % e)

    new = []
    kind = text_kind(ctype, ext)
    if owner and kind and len(body) <= PARSE_MAX:
        cands, exts = extract(body.decode("utf-8", "replace"), final_c, kind)
        with lock:
            for tag, host in exts:
                ext_hosts[(tag, host)] += 1
        for c in cands:
            t = register(c, rel_final)
            if t:
                new.append(t)
    return new


def crawl(seed_urls):
    tasks = deque()
    for u in seed_urls:
        c = canon(u)
        t = register(c, None) if c else None
        if t:
            tasks.append(t)
    futures, done_n = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        while tasks or futures:
            if MAX_SECONDS and tasks and time.time() - T0 > MAX_SECONDS:
                flags["zaman sınırı doldu (%d sn); kuyrukta %d adres alınmadan kaldı" % (MAX_SECONDS, len(tasks))] = True
                tasks.clear()
            while tasks and len(futures) < WORKERS * 3:
                t = tasks.popleft()
                futures[ex.submit(handle, t)] = t
            if not futures:
                break
            qstate.update(queue=len(tasks), inflight=len(futures), done=done_n)
            done, _ = wait(list(futures), timeout=5, return_when=FIRST_COMPLETED)
            for f in done:
                t = futures.pop(f)
                try:
                    new = f.result()
                except Exception as e:
                    new = fail(t[0], "beklenmeyen hata: %r" % (e,))
                tasks.extend(new or [])
                done_n += 1
                if done_n % 200 == 0:
                    print("  ... %d adres işlendi, %d dosya, %.0f MB, kuyruk %d" %
                          (done_n, len(written), stats["bytes"] / 1048576.0, len(tasks)), flush=True)
    qstate.update(queue=0, inflight=0, done=done_n)


def rest_seeds():
    """WordPress REST API'den (varsa) tüm sayfa/yazı adreslerini ve medya dosyalarını listeler."""
    urls = []
    for ep, field in (("pages", "link"), ("posts", "link"), ("media", "source_url"),
                      ("categories", "link"), ("tags", "link")):
        page = 1
        while page <= 60:
            u = "%s/wp-json/wp/v2/%s?per_page=100&page=%d&_fields=%s" % (BASE, ep, page, field)
            r = fetch(u)
            if r.get("error") or r["status"] != 200:
                break
            try:
                data = json.loads(r["body"].decode("utf-8", "replace"))
            except ValueError:
                break
            if not isinstance(data, list) or not data:
                break
            urls += [it[field] for it in data if isinstance(it, dict) and it.get(field)]
            try:
                total = int(r["headers"].get("X-WP-TotalPages", "1") or 1)
            except ValueError:
                total = 1
            if page >= total:
                break
            page += 1
    return urls


# --------------------------------------------------------------------------- denetim
KNOWN_HOSTS = ("google.com", "google-analytics.com", "googletagmanager.com", "gstatic.com", "googleapis.com",
               "googleusercontent.com", "youtube.com", "youtube-nocookie.com", "youtu.be", "ytimg.com",
               "facebook.com", "facebook.net", "fbcdn.net", "instagram.com", "twitter.com", "x.com",
               "linkedin.com", "vimeo.com", "gravatar.com", "wp.com", "wordpress.org", "w.org",
               "jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com", "jquery.com", "fontawesome.com",
               "bootstrapcdn.com", "recaptcha.net", "doubleclick.net", "g.co", "maps.google.com")

SUSP_HTML = [
    ("eval(", re.compile(r"\beval\s*\(")),
    ("atob(", re.compile(r"\batob\s*\(")),
    ("String.fromCharCode", re.compile(r"String\.fromCharCode")),
    ("document.write(unescape", re.compile(r"document\.write\s*\(\s*unescape", re.I)),
    ("uzun \\x dizisi", re.compile(r"(?:\\x[0-9a-fA-F]{2}){12,}")),
    ("boyutsuz/gizli iframe", re.compile(r"<iframe[^>]+(?:width|height)\s*=\s*[\"']?[01][\"'\s>]", re.I)),
    ("</html> sonrası içerik", re.compile(r"</html>\s*(?:<script|<iframe|<div|<a\s)", re.I)),
    ("meta refresh", re.compile(r"<meta[^>]+http-equiv\s*=\s*[\"']?refresh", re.I)),
    ("sj887po", re.compile(r"sj887po")),
    ("spam anahtar kelime", re.compile(r"viagra|cialis|casino|\bporn|slot gacor|\bjudi\b|togel|bahis", re.I)),
]
SUSP_ANY = [
    ("document.write(unescape", re.compile(r"document\.write\s*\(\s*unescape", re.I)),
    ("sj887po", re.compile(r"sj887po")),
    ("spam anahtar kelime", re.compile(r"viagra|cialis|slot gacor|\bjudi\b|togel", re.I)),
]
HTTP_SELF = re.compile(r"http://(?:www\.)?" + re.escape(HP), re.I) if BASE.startswith("https") else None


def scan_files():
    findings, http_refs = [], 0
    for root, _dirs, files in os.walk(OUT):
        for fn in files:
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext not in ("html", "htm", "js", "css", "svg", "txt", "json", "xml"):
                continue
            full = os.path.join(root, fn)
            try:
                if os.path.getsize(full) > 4_000_000:
                    continue
                with open(full, "rb") as f:
                    text = f.read().decode("utf-8", "replace")
            except OSError:
                continue
            rel = os.path.relpath(full, OUT)
            if rel in stub_files:
                continue
            is_html = ext in ("html", "htm")
            for name, rx in (SUSP_HTML if is_html else SUSP_ANY):
                ms = list(rx.finditer(text))
                if ms:
                    m = ms[0]
                    snip = re.sub(r"\s+", " ", text[max(0, m.start() - 40): m.end() + 60])[:140]
                    findings.append((rel, name, len(ms), snip))
            if is_html and HTTP_SELF:
                http_refs += len(HTTP_SELF.findall(text))
    return findings, http_refs


def saved(key):
    return key in results or alias.get(key) in results


def sitemap_urls():
    urls = set()
    for key, r in results.items():
        p = unq(urllib.parse.urlsplit(key).path)
        if re.search(r"/(?:wp-)?sitemap[^/]*\.xml$", p) and r.get("file") and not r.get("alias_of"):
            try:
                with open(os.path.join(OUT, r["file"]), "rb") as f:
                    txt = f.read().decode("utf-8", "replace")
            except OSError:
                continue
            for m in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>", txt):
                c = canon(html.unescape(m.group(1)))
                if c and not c.split("?")[0].endswith(".xml"):
                    urls.add(c)
    return urls


def mb(n):
    return "%.1f MB" % (n / 1048576.0)


def build_report(t0, ips, home_ok):
    ozet = {"ok": bool(home_ok), "site": SITE, "ips": ips}
    L = []
    add = L.append
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    add("# %s statik kopya raporu" % APEX)
    add("")
    add("- Tarih: %s, süre: %d sn" % (now, time.time() - t0))
    ip_note = ""
    if EXPECT_IP:
        ip_note = " (beklenen %s: %s)" % (EXPECT_IP, "EŞLEŞTİ" if EXPECT_IP in ips else "EŞLEŞMEDİ")
        ozet["ip_match"] = EXPECT_IP in ips
    add("- Sitenin çözümlendiği IP: %s%s" % (", ".join(ips) or "?", ip_note))
    if not home_ok:
        add("")
        add("**Ana sayfa alınamadı, kopya yapılmadı.** Ayrıntı: %s" % failed.get(BASE + "/", "?"))
        ozet["error"] = failed.get(BASE + "/", "ana sayfa alınamadı")
        return "\n".join(L) + "\n", ozet

    total = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(OUT) for f in fs)
    cat = Counter()
    for key, r in results.items():
        if r.get("alias_of"):
            continue
        t = r.get("type", "")
        p = unq(urllib.parse.urlsplit(key).path).lower()
        e = posixpath.splitext(p)[1].lstrip(".")
        if t in HTML_TYPES:
            cat["HTML sayfa"] += 1
        elif e in IMAGE_EXT:
            cat["görsel"] += 1
        elif e in ("css",):
            cat["CSS"] += 1
        elif e in ("js", "mjs"):
            cat["JS"] += 1
        elif e in ("woff", "woff2", "ttf", "otf", "eot"):
            cat["font"] += 1
        elif e in ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "csv", "rtf", "zip", "rar", "7z"):
            cat["doküman/arşiv"] += 1
        else:
            cat["diğer"] += 1
    add("- Toplam: %d dosya, %s" % (len(written), mb(total)))
    add("- Türler: " + ", ".join("%s %d" % (k, v) for k, v in cat.most_common()))
    add("- Sunucu yanıtları: " + ", ".join("%s: %d" % kv for kv in status_counter.most_common(10)))
    ozet.update(files=len(written), bytes=total, categories=dict(cat))

    sm = sitemap_urls()
    sm_missing = sorted(u for u in sm if not (plan(u)[0] and saved(plan(u)[0][0])))
    add("")
    add("## Sitemap kontrolü")
    add("Sitemap'lerde %d adres var; %d tanesi kopyalandı, %d tanesi kopyalanamadı." %
        (len(sm), len(sm) - len(sm_missing), len(sm_missing)))
    for u in sm_missing[:60]:
        t, _w = plan(u)
        add("- %s  ->  %s" % (u, failed.get(t[0], "kopyalanmadı") if t else "atlandı"))
    ozet.update(sitemap_total=len(sm), sitemap_missing=sm_missing)

    real_fail = {k: v for k, v in failed.items() if k not in OPTIONAL}
    add("")
    add("## Çekilemeyen adresler (%d)" % len(real_fail))
    for k, v in sorted(real_fail.items())[:200]:
        add("- %s  ->  %s" % (k, v))
    ozet["failed"] = dict(sorted(real_fail.items()))

    missing = defaultdict(set)
    for src, cs in refs.items():
        for c in cs:
            task, _w = plan(c)
            if task is not None and not saved(task[0]) and task[0] not in OPTIONAL:
                missing[task[0]].add(src)
    add("")
    add("## Sayfaların başvurduğu ama kopyada olmayan kaynaklar (%d)" % len(missing))
    for k, srcs in sorted(missing.items())[:200]:
        add("- %s  (%s)  <- %d dosya, ör. %s" % (k, failed.get(k, "?"), len(srcs), sorted(srcs)[0]))
    ozet["missing_refs"] = {k: sorted(v)[:3] for k, v in sorted(missing.items())}

    add("")
    add("## Bilerek kopyalanmayan dinamik adresler")
    for why, urls in sorted(skipped.items()):
        ex = sorted(urls)[:5]
        add("- %s: %d adres, ör. %s" % (why, len(urls), ", ".join(ex)))
    ozet["skipped"] = {w: len(u) for w, u in skipped.items()}

    if oversize:
        add("")
        add("## Boyut sınırı nedeniyle atlanan dosyalar")
        for k, v in oversize.items():
            add("- %s (%s)" % (k, mb(v)))
    if empty:
        add("")
        add("## Boş (0 bayt) gelen dosyalar (%d)" % len(empty))
        add("Sunucu bunları boş döndürdü; kaynak sitede de boş olabilir (bozuk yükleme).")
        for k, hd in sorted(empty.items()):
            add("- %s  %s" % (k, json.dumps(hd, ensure_ascii=False)))
    ozet["empty_files"] = dict(sorted(empty.items()))
    if collisions:
        add("")
        add("## Aynı dosya yoluna düşen farklı içerikler (ilki tutuldu)")
        for rel, k in collisions[:50]:
            add("- %s <- %s" % (rel, k))
    if flags:
        add("")
        add("## Uyarılar")
        for k in flags:
            add("- " + k)
    ozet.update(oversize=oversize, collisions=len(collisions), flags=list(flags))

    hosts = defaultdict(lambda: [Counter(), 0])
    for (tag, host), n in ext_hosts.items():
        hosts[host][0][tag] += n
        hosts[host][1] += n
    add("")
    add("## Sayfaların dışarıdan yüklediği kaynaklar (script/css/iframe/görsel)")
    unknown = []
    for host, (tags, n) in sorted(hosts.items(), key=lambda x: -x[1][1]):
        known = any(host == h or host.endswith("." + h) for h in KNOWN_HOSTS)
        mark = "" if known else "  ⚠ tanınmayan"
        if not known and (tags["script"] or tags["iframe"] or tags["link"]):
            unknown.append(host)
        add("- %s: %s%s" % (host, ", ".join("%s %d" % kv for kv in tags.most_common()), mark))
    ozet["external_hosts"] = {h: v[1] for h, v in hosts.items()}
    ozet["unknown_external_hosts"] = unknown

    findings, http_refs = scan_files()
    add("")
    add("## Dikkat: şüpheli kod belirtisi olan dosyalar (%d)" % len(findings))
    add("Bunlar otomatik tarama sonucudur; her biri zararlı demek değildir, bakılmalıdır.")
    for rel, name, n, snip in findings[:120]:
        add("- `%s`: %s x%d  |  %s" % (rel, name, n, snip))
    ozet["findings"] = [dict(file=a, pattern=b, count=c, sample=d) for a, b, c, d in findings]
    if HTTP_SELF is not None:
        add("")
        add("- HTML dosyalarında `http://%s` ile başlayan kendi adres başvurusu: %d" % (APEX, http_refs))
        ozet["http_self_refs"] = http_refs

    known_pages = set(sm) | rest_known
    extra = [key for key, r in results.items()
             if r.get("type") in HTML_TYPES and not r.get("alias_of") and key not in known_pages]
    add("")
    add("## Sitemap'te olmayıp bağlantılardan bulunan sayfalar (%d)" % len(extra))
    for k in sorted(extra)[:200]:
        add("- " + k)
    ozet["pages_not_in_sitemap"] = sorted(extra)

    big = sorted(((os.path.getsize(os.path.join(OUT, rel)), rel) for rel in written
                  if os.path.exists(os.path.join(OUT, rel))), reverse=True)[:15]
    add("")
    add("## En büyük dosyalar")
    for n, rel in big:
        add("- %s  %s" % (mb(n), rel))
    return "\n".join(L) + "\n", ozet


# --------------------------------------------------------------------------- ilerleme
def snapshot(final=False):
    """Şu anki durumun kısa özeti (Actions günlüğüne erişemediğimde buradan izliyorum)."""
    with lock:
        d = {
            "saat": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "gecen_sn": int(time.time() - T0),
            "dosya": len(written),
            "mb": round(stats["bytes"] / 1048576.0, 1),
            "sayfa": stats["pages"],
            "kuyruk": qstate["queue"],
            "calisan": qstate["inflight"],
            "islenen": qstate["done"],
            "basarisiz": len(failed),
            "yanitlar": {str(k): v for k, v in status_counter.most_common(8)},
            "son_kaydedilen": [list(x) for x in list(recent)[-6:]],
            "son_basarisiz": [[k[-80:], str(v)[:80]] for k, v in list(failed.items())[-6:]],
            "uyarilar": list(flags),
            "bitti": bool(final),
        }
    return d


def push_progress(d):
    """İlerleme dosyasını 'progress' dalına gönderir (ana dala dokunmaz, çalışma klasörünü değiştirmez)."""
    if not PROGRESS_PUSH:
        return
    try:
        path = os.path.join(tempfile.gettempdir(), "mirror_progress.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        env = dict(os.environ, GIT_AUTHOR_NAME="mirror-bot", GIT_COMMITTER_NAME="mirror-bot",
                   GIT_AUTHOR_EMAIL="bot@users.noreply.github.com",
                   GIT_COMMITTER_EMAIL="bot@users.noreply.github.com")

        def git(*a, inp=None):
            return subprocess.run(["git", *a], input=inp, env=env, capture_output=True, text=True,
                                  timeout=90, check=True).stdout.strip()

        blob = git("hash-object", "-w", path)
        tree = git("mktree", inp="100644 blob %s\tprogress.json\n" % blob)
        commit = git("commit-tree", tree, "-m", "ilerleme %s" % d["saat"])
        git("push", "-f", "origin", "%s:refs/heads/progress" % commit)
    except Exception as e:    # ilerleme gönderilemese de indirme sürsün
        print("  (ilerleme gönderilemedi: %s)" % str(e)[:150], flush=True)


def progress_loop(stop):
    while not stop.wait(PROGRESS_EVERY):
        d = snapshot()
        print("  [ilerleme] %(gecen_sn)s sn, %(dosya)s dosya, %(mb)s MB, kuyruk %(kuyruk)s, "
              "başarısız %(basarisiz)s" % d, flush=True)
        push_progress(d)


# --------------------------------------------------------------------------- ana akış
def main():
    t0 = time.time()
    try:
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(HOST, None)})
    except OSError:
        ips = []
    print("Site: %s  IP: %s" % (SITE, ", ".join(ips) or "?"), flush=True)

    home = fetch(BASE + "/")
    home_ok = not home.get("error") and home["status"] == 200 and home["ctype"] in HTML_TYPES
    os.makedirs(REPORT, exist_ok=True)
    if not home_ok:
        failed[BASE + "/"] = home.get("error") or "HTTP %s (%s)" % (home["status"], home["ctype"])
        text, ozet = build_report(t0, ips, False)
        with open(os.path.join(REPORT, "rapor.md"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(REPORT, "ozet.json"), "w", encoding="utf-8") as f:
            json.dump(ozet, f, ensure_ascii=False, indent=1)
        print("Ana sayfa alınamadı:", failed[BASE + "/"])
        sys.exit(2)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    seeds = [BASE + p for p in ("/", "/robots.txt", "/favicon.ico", "/wp-sitemap.xml", "/wp-sitemap.xsl",
                                "/sitemap.xml", "/sitemap_index.xml")]
    rest = rest_seeds()
    rest_known.update(c for c in (canon(u) for u in rest) if c)
    seeds += rest
    for p in re.split(r"[\s,]+", os.environ.get("EXTRA_PATHS", "").strip()):   # JS'in çalışırken ürettiği adresler vb.
        if p:
            seeds.append(BASE + (p if p.startswith("/") else "/" + p))
    print("Tarama başlıyor (%d başlangıç adresi)" % len(seeds), flush=True)
    stop = threading.Event()
    threading.Thread(target=progress_loop, args=(stop,), daemon=True).start()
    try:
        crawl(seeds)
    finally:
        stop.set()

    nf = os.path.join(OUT, "page-not-found", "index.html")
    if os.path.isfile(nf):
        shutil.copyfile(nf, os.path.join(OUT, "404.html"))
    open(os.path.join(OUT, ".nojekyll"), "w").close()

    text, ozet = build_report(t0, ips, True)
    with open(os.path.join(REPORT, "rapor.md"), "w", encoding="utf-8") as f:
        f.write(text)
    manifest = {k: v for k, v in sorted(results.items())}
    with open(os.path.join(REPORT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(REPORT, "ozet.json"), "w", encoding="utf-8") as f:
        json.dump(ozet, f, ensure_ascii=False, indent=1)

    print("\n" + text, flush=True)
    push_progress(snapshot(final=True))
    sys.exit(0 if len(written) >= 5 else 2)


if __name__ == "__main__":
    main()
