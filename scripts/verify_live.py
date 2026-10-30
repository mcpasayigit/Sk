#!/usr/bin/env python3
"""
Canlı site kontrolü (ek paket gerektirmez).

report/manifest.json'daki her dosyayı sunucudan çeker ve SHA-256 değerini kopyayla karşılaştırır.
Ayrıca yönlendirmeleri (http -> https, www -> çıplak alan adı), 404 sayfasını ve dışarıdan
görünmemesi gereken yolları dener.

  python3 scripts/verify_live.py --host suleymankaynak.com [--ip 1.2.3.4] [--limit N]

--ip verilirse DNS yerine bu IP'ye bağlanılır (Host ve SNI yine --host olur, sertifika doğrulanmaz).
DNS'i değiştirmeden yeni sunucuyu denemek içindir. Çıkış kodu: 0 = her şey tamam, 1 = sorun var.
"""
import argparse
import concurrent.futures as cf
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.parse

UA = "canli-kontrol/1.0"
JS_TYPES = {"application/javascript", "text/javascript", "application/x-javascript", "application/ecmascript"}
XML_TYPES = {"text/xml", "application/xml", "application/xslt+xml", "text/xsl"}
SHOULD_BE_HIDDEN = ["/.cpanel.yml", "/.git/HEAD", "/deploy/htaccess", "/report/manifest.json",
                    "/README.md", "/wp-login.php", "/xmlrpc.php"]


class Cfg:
    def __init__(self, host, ip, http_port, https_port, timeout):
        self.host, self.ip = host, ip
        self.http_port, self.https_port, self.timeout = http_port, https_port, timeout


class IPSNIConnection(http.client.HTTPSConnection):
    """IP adresine bağlanır, SNI olarak alan adını gönderir, sertifikayı doğrulamaz."""

    def __init__(self, ip, port, sni, timeout):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        super().__init__(ip, port, timeout=timeout, context=ctx)
        self._sni = sni

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._sni)


def fetch(cfg, path, host=None, scheme="https"):
    host = host or cfg.host
    if scheme == "https":
        port = cfg.https_port
        if cfg.ip:
            conn = IPSNIConnection(cfg.ip, port, host, cfg.timeout)
        else:
            conn = http.client.HTTPSConnection(host, port, timeout=cfg.timeout, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(cfg.ip or host, cfg.http_port, timeout=cfg.timeout)
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": UA, "Accept": "*/*",
                                           "Accept-Encoding": "identity", "Connection": "close"})
        r = conn.getresponse()
        body = r.read()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body
    finally:
        conn.close()


def fetch_retry(cfg, path, host=None, scheme="https", tries=3):
    err = None
    for i in range(tries):
        try:
            st, hd, body = fetch(cfg, path, host, scheme)
            if st >= 500 and i < tries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            return st, hd, body, None
        except (OSError, http.client.HTTPException) as e:
            err = "%s: %s" % (type(e).__name__, str(e)[:140])
            time.sleep(1.5 * (i + 1))
    return None, {}, b"", err


def annot(level, title, msg):
    """GitHub Actions ekranında görünen kısa not (notice / warning / error)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::%s title=%s::%s" % (level, title, msg))


def type_ok(expected, got):
    e = (expected or "").split(";")[0].strip().lower()
    g = (got or "").split(";")[0].strip().lower()
    if not e or e == g:
        return True
    if e in JS_TYPES and g in JS_TYPES:
        return True
    if e in XML_TYPES and g in XML_TYPES:
        return True
    if "font" in e:
        return True
    if e.startswith("image/"):
        return g.startswith("image/")
    return False


def check_entry(cfg, url, rec):
    """(durum, url, ayrıntı) döndürür; sorun yoksa None. Tür uyarısı için ('uyari', ...)."""
    path = urllib.parse.urlsplit(url).path or "/"
    alias = "alias_of" in rec
    if alias:                      # özgün sunucu başka adrese yönlendiriyordu; kopyada küçük bir yönlendirme sayfası var
        path = path.rstrip("/") + "/"
    st, hd, body, err = fetch_retry(cfg, path)
    if err:
        return ("hata", url, err)
    if st != 200:
        return ("durum", url, "HTTP %s" % st)
    if alias:
        return None
    if hashlib.sha256(body).hexdigest() != rec["sha256"]:
        return ("fark", url, "%d bayt geldi, %d bekleniyordu" % (len(body), rec["bytes"]))
    if not type_ok(rec.get("type"), hd.get("content-type")):
        return ("uyari", url, "tür: beklenen %s, gelen %s" % (rec.get("type"), hd.get("content-type")))
    return None


def behaviour_checks(cfg, manifest, site404):
    h = cfg.host
    out = []

    def add(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    st, hd, body, err = fetch_retry(cfg, "/kontrol-olmayan-sayfa-xyz/")
    same = site404 is None or body == site404
    add("Olmayan sayfa 404 verir ve 404.html gösterilir", err is None and st == 404 and same,
        err or "HTTP %s%s" % (st, "" if same else ", gövde 404.html'den farklı"))

    st, hd, _b, err = fetch_retry(cfg, "/", scheme="http")
    loc = hd.get("location", "")
    add("http://%s/ -> https://%s/ (301)" % (h, h), err is None and st == 301 and loc == "https://%s/" % h,
        err or "HTTP %s %s" % (st, loc))

    pages = [urllib.parse.urlsplit(u).path for u, r in manifest.items()
             if r.get("type", "").startswith("text/html") and u.endswith("/") and urllib.parse.urlsplit(u).path != "/"]
    sample = pages[0] if pages else "/"
    st, hd, _b, err = fetch_retry(cfg, sample, host="www." + h, scheme="http")
    loc = hd.get("location", "")
    add("http://www.%s%s -> https://%s%s (301)" % (h, sample, h, sample),
        err is None and st == 301 and loc == "https://%s%s" % (h, sample), err or "HTTP %s %s" % (st, loc))

    st, hd, _b, err = fetch_retry(cfg, "/", host="www." + h)
    loc = hd.get("location", "")
    add("https://www.%s/ -> https://%s/ (301)" % (h, h), err is None and st == 301 and loc == "https://%s/" % h,
        err or "HTTP %s %s" % (st, loc))

    d = sample if pages else None
    if d:
        st, hd, _b, err = fetch_retry(cfg, d.rstrip("/"))
        loc_path = urllib.parse.urlsplit(hd.get("location", "")).path
        add("%s (eğik çizgisiz) -> %s yönlenir" % (d.rstrip("/"), d), err is None and st in (301, 308) and loc_path == d,
            err or "HTTP %s %s" % (st, hd.get("location", "")))

    exposed = []
    for p in SHOULD_BE_HIDDEN:
        st, _h, _b, err = fetch_retry(cfg, p, tries=2)
        if err is None and st == 200:
            exposed.append(p)
    add("Depo dosyaları ve WordPress giriş yolları dışarıdan görünmüyor", not exposed,
        "Açık görünenler: " + ", ".join(exposed) if exposed else "hepsi 404/yönlendirme")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="suleymankaynak.com")
    ap.add_argument("--ip", default="")
    ap.add_argument("--manifest", default="report/manifest.json")
    ap.add_argument("--site-dir", default="site")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="yalnızca bu kadar dosyayı (dağıtılmış örnek) dene; 0 = hepsi")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--timeout", type=float, default=60)
    a = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9.-]+", a.host):
        sys.exit("Geçersiz alan adı: %r" % a.host)
    if a.ip:
        try:
            ipaddress.ip_address(a.ip)
        except ValueError:
            sys.exit("Geçersiz IP adresi: %r" % a.ip)

    manifest = json.load(open(a.manifest, encoding="utf-8"))
    own = {a.host, "www." + a.host}
    items = [(u, r) for u, r in manifest.items() if (urllib.parse.urlsplit(u).hostname or "").lower() in own]
    if a.limit and a.limit < len(items):
        step = len(items) / float(a.limit)
        items = [items[int(i * step)] for i in range(a.limit)]
    p404 = os.path.join(a.site_dir, "404.html")
    site404 = open(p404, "rb").read() if os.path.isfile(p404) else None

    cfg = Cfg(a.host, a.ip, a.http_port, a.https_port, a.timeout)
    where = "IP %s üzerinden" % a.ip if a.ip else "DNS'in gösterdiği sunucudan"
    print("Kontrol: %s, %s, %d dosya%s" % (a.host, where, len(items), " (örnek)" if a.limit else ""), flush=True)

    t0 = time.time()
    problems, warnings = [], []
    done = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for res in ex.map(lambda it: check_entry(cfg, it[0], it[1]), items):
            done += 1
            if res:
                (warnings if res[0] == "uyari" else problems).append(res)
            if done % 200 == 0:
                print("  ... %d/%d (sorun %d)" % (done, len(items), len(problems)), flush=True)
    behav = behaviour_checks(cfg, manifest, site404)
    secs = time.time() - t0

    counts = {}
    for k, _u, _d in problems:
        counts[k] = counts.get(k, 0) + 1
    ok_n = len(items) - len(problems)
    bad_behav = [b for b in behav if not b[1]]

    L = []
    L.append("## Canlı kontrol: %s (%s)" % (a.host, where))
    L.append("")
    L.append("- Dosya: **%d** denendi, **%d** tamam%s" % (
        len(items), ok_n, "" if not problems else ", sorun: " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items()))))
    L.append("- Davranış kontrolleri: **%d/%d** tamam" % (len(behav) - len(bad_behav), len(behav)))
    L.append("- Süre: %.0f sn" % secs)
    L.append("")
    L.append("| | Kontrol | Ayrıntı |")
    L.append("|---|---|---|")
    for name, ok, detail in behav:
        L.append("| %s | %s | %s |" % ("✅" if ok else "❌", name, detail.replace("|", "/")))
    if problems:
        L.append("")
        L.append("### Sorunlu dosyalar (ilk 30)")
        for k, u, d in problems[:30]:
            L.append("- `%s` %s: %s" % (k, u, d))
    if warnings:
        L.append("")
        L.append("### Uyarılar (ilk 15; hata sayılmaz)")
        for _k, u, d in warnings[:15]:
            L.append("- %s: %s" % (u, d))
    L.append("")
    L.append("**Sonuç: %s**" % ("TAMAM, site kopyayla bayt bayt aynı" if not problems and not bad_behav and not a.limit
                                 else ("TAMAM (yalnızca örnek denendi)" if not problems and not bad_behav else "SORUN VAR")))
    text = "\n".join(L)
    print(text)
    for name, ok, detail in behav:
        annot("notice" if ok else "error", "Kontrol", "%s: %s (%s)" % ("TAMAM" if ok else "SORUN", name, detail))
    for k, u, d in problems[:max(0, 9 - len(bad_behav))]:
        annot("error", "Dosya", "%s %s: %s" % (k, u, d))
    for _k, u, d in warnings[:3]:
        annot("warning", "Uyari", "%s: %s" % (u, d))
    annot("notice", "Sonuc", "%d/%d dosya tamam, davranis %d/%d tamam, %.0f sn" % (
        ok_n, len(items), len(behav) - len(bad_behav), len(behav), secs))
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(1 if (problems or bad_behav) else 0)


if __name__ == "__main__":
    main()
