#!/usr/bin/env python3
"""Sitenin kendi "sayfa bulunamadı" sayfasını alıp site/404.html olarak kaydeder.

Olmayan bir adrese sunucunun verdiği 404 yanıtının gövdesi bayt bayt aynen kaydedilir
(mirror.py içindeki grab_404 kullanılır). Adres SITE_URL ortam değişkeninden gelir.
"""
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mirror  # noqa: E402


def main():
    if not mirror.grab_404():
        print("::error title=404 sayfası alınamadı::Sunucu olmayan adrese HTML içerikli 404 vermedi")
        return 1
    data = open(os.path.join(mirror.OUT, "404.html"), "rb").read()
    text = data.decode("utf-8", "replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else "?"
    print("::notice title=404 sayfası::%d bayt, sha256 %s, başlık: %s"
          % (len(data), hashlib.sha256(data).hexdigest()[:16], title[:80]))
    hosts = sorted(set(re.findall(r"<script[^>]+src=[\"']https?://([^/\"']+)", text, re.I)))
    print("::notice title=404 sayfasındaki dış script alan adları::%s" % (", ".join(hosts) or "yok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
