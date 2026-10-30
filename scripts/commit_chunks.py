#!/usr/bin/env python3
"""site/ ve report/ klasörlerini repoya parça parça gönderir.

GitHub tek bir push'ta yaklaşık 2 GB'a kadar kabul eder; büyük kopyayı güvenli olsun diye
CHUNK_MB (varsayılan 250) büyüklüğünde parçalara bölüp her parçayı ayrı commit + push yapar.
Ortam değişkenleri: CHUNK_MB, MAX_TOTAL_MB (varsayılan 1200), BRANCH (varsayılan main).
"""
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit

CHUNK = int(os.environ.get("CHUNK_MB", "250")) * 1024 * 1024
MAX_TOTAL = int(os.environ.get("MAX_TOTAL_MB", "1200")) * 1024 * 1024
MAX_FILE = 95 * 1024 * 1024
BRANCH = os.environ.get("BRANCH", "main")
ENV = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
STAMP = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
LABEL = urlsplit(os.environ.get("SITE_URL", "https://suleymankaynak.com")).hostname or "site"


def git(*args, inp=None, check=True):
    r = subprocess.run(["git", *args], input=inp, env=ENV, capture_output=True)
    if check and r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-1500:])
        raise SystemExit("git %s başarısız (kod %d)" % (" ".join(args[:2]), r.returncode))
    return r


def staged():
    return git("diff", "--cached", "--quiet", check=False).returncode != 0


def push():
    for i in range(5):
        r = git("push", "origin", "HEAD:" + BRANCH, check=False)
        if r.returncode == 0:
            return
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-800:])
        time.sleep(8 * (i + 1))
    raise SystemExit("git push 5 denemede de başarısız oldu")


def main():
    os.makedirs("site", exist_ok=True)
    os.makedirs("report", exist_ok=True)

    files, total = [], 0
    for root, _dirs, names in os.walk("site"):
        for name in names:
            p = os.path.join(root, name)
            if name.endswith(".indiriliyor") or os.path.islink(p):
                continue
            n = os.path.getsize(p)
            if n > MAX_FILE:
                print("atlandı (GitHub 100 MB sınırı): %s  %.1f MB" % (p, n / 1048576.0))
                continue
            files.append((p, n))
            total += n
    files.sort()
    print("site/: %d dosya, %.0f MB" % (len(files), total / 1048576.0), flush=True)

    # 1) rapor (küçük; her durumda gönderilsin)
    git("add", "-A", "--", "report")
    if staged():
        git("commit", "-q", "-m", "%s raporu %s UTC" % (LABEL, STAMP))
        push()
        print("rapor gönderildi", flush=True)

    if total > MAX_TOTAL:
        print("::error::site/ klasörü %.0f MB, sınır %d MB; yalnızca rapor gönderildi"
              % (total / 1048576.0, MAX_TOTAL // 1048576))
        raise SystemExit(1)

    # 2) önceki kopyada olup bu seferde olmayan dosyalar silinsin
    gone = git("ls-files", "-z", "--deleted", "--", "site").stdout
    if gone:
        git("update-index", "-z", "--remove", "--stdin", inp=gone)
        if staged():
            git("commit", "-q", "-m", "%s kopyası %s UTC (silinenler)" % (LABEL, STAMP))
            print("silinen dosyalar işlendi", flush=True)

    # 3) dosyalar parça parça
    state = {"batch": [], "size": 0, "n": 0}

    def flush():
        batch = state["batch"]
        if not batch:
            return
        data = b"\0".join(p.encode("utf-8", "surrogateescape") for p in batch) + b"\0"
        git("add", "--pathspec-from-file=-", "--pathspec-file-nul", inp=data)
        if staged():
            state["n"] += 1
            git("commit", "-q", "-m", "%s kopyası %s UTC (parça %d)" % (LABEL, STAMP, state["n"]))
            push()
            print("parça %d gönderildi (%d dosya tarandı)" % (state["n"], len(batch)), flush=True)
        state["batch"], state["size"] = [], 0

    for p, n in files:
        state["batch"].append(p)
        state["size"] += n
        if state["size"] >= CHUNK:
            flush()
    flush()
    # silinenler dışında gönderilecek bir şey kalmadıysa bile bekleyen commit'ler gitsin
    push()
    print("tamam: %d parça" % state["n"], flush=True)


if __name__ == "__main__":
    main()
