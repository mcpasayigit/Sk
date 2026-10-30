#!/usr/bin/env python3
"""TLS el sıkışması denemeleri: hangi istemci ayarı sunucuya ulaşıyor, hangisi zaman aşımına uğruyor.
   python3 scripts/tls_probe.py suleymankaynak.com  ->  stdout"""
import socket, ssl, subprocess, sys, threading, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "suleymankaynak.com"
TIMEOUT, TRIES = 7, 2


def ctx_default():
    return ssl.create_default_context()


def ctx_max12():
    c = ssl.create_default_context(); c.maximum_version = ssl.TLSVersion.TLSv1_2; return c


def ctx_alpn():
    c = ssl.create_default_context(); c.set_alpn_protocols(["http/1.1"]); return c


def ctx_alpn_h2():
    c = ssl.create_default_context(); c.set_alpn_protocols(["h2", "http/1.1"]); return c


def ctx_cipher_one():
    c = ssl.create_default_context(); c.maximum_version = ssl.TLSVersion.TLSv1_2
    c.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256"); return c


def ctx_seclevel0():
    c = ssl.create_default_context(); c.set_ciphers("ALL:@SECLEVEL=0"); return c


def ctx_noticket():
    c = ssl.create_default_context(); c.options |= ssl.OP_NO_TICKET; return c


def ctx_alpn_noticket_max12():
    c = ssl.create_default_context(); c.maximum_version = ssl.TLSVersion.TLSv1_2
    c.options |= ssl.OP_NO_TICKET; c.set_alpn_protocols(["h2", "http/1.1"]); return c


CTXS = [("py varsayılan", ctx_default), ("py TLS<=1.2", ctx_max12), ("py ALPN http/1.1", ctx_alpn),
        ("py ALPN h2+http/1.1", ctx_alpn_h2), ("py tek şifre TLS1.2", ctx_cipher_one),
        ("py tüm şifreler", ctx_seclevel0), ("py biletsiz", ctx_noticket),
        ("py ALPN+biletsiz+TLS1.2", ctx_alpn_noticket_max12)]


def hello_len_for(client):
    """Yerel dinleyiciye bağlanıp ClientHello'nun kayıt uzunluğunu okur."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    box = {}

    def serve():
        try:
            c, _ = srv.accept(); c.settimeout(5)
            hdr = c.recv(5)
            box["n"] = int.from_bytes(hdr[3:5], "big") + 5 if len(hdr) == 5 else None
            c.close()
        except Exception:
            box["n"] = None
        srv.close()

    t = threading.Thread(target=serve, daemon=True); t.start()
    try:
        client(port)
    except Exception:
        pass
    t.join(6)
    return box.get("n")


def py_try(factory, port=None, host_ip=None):
    s = socket.create_connection((host_ip or HOST, port or 443), timeout=TIMEOUT)
    s.settimeout(TIMEOUT)
    t = factory().wrap_socket(s, server_hostname=HOST)
    ver, cip = t.version(), t.cipher()[0]
    t.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nConnection: close\r\nAccept-Encoding: identity\r\n\r\n" % HOST).encode())
    first = t.recv(40)
    t.close()
    return "%s %s %r" % (ver, cip, first[:17])


def run_curl(args, port=None):
    url = "https://%s/" % HOST
    cmd = ["curl", "-sS", "-m", str(TIMEOUT), "-o", "/dev/null", "-w", "%{http_code} tls=%{time_appconnect}s"]
    if port:
        cmd += ["--resolve", "%s:%d:127.0.0.1" % (HOST, port), "-k"]
        url = "https://%s:%d/" % (HOST, port)
    r = subprocess.run(cmd + args + [url], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip().replace("\n", " ")[:90]


CURLS = [("curl varsayılan", []), ("curl --http1.1", ["--http1.1"]), ("curl TLS1.2 (min+max)", ["--tlsv1.2", "--tls-max", "1.2"]),
         ("curl --tls-max 1.2", ["--tls-max", "1.2"]), ("curl --no-alpn", ["--no-alpn"]),
         ("curl tek şifre TLS1.2", ["--tls-max", "1.2", "--ciphers", "ECDHE-RSA-AES128-GCM-SHA256"])]

print("# TLS denemeleri: %s (%s)" % (HOST, time.strftime("%F %T", time.gmtime())))
print("| istemci | ClientHello (bayt) | sonuç |\n|---|---|---|")
for name, f in CTXS:
    n = hello_len_for(lambda port, f=f: py_try(f, port=port, host_ip="127.0.0.1"))
    out = []
    for i in range(TRIES):
        t0 = time.time()
        try:
            out.append("OK %s (%.1fs)" % (py_try(f), time.time() - t0))
        except Exception as e:
            out.append("HATA %s (%.1fs)" % (type(e).__name__, time.time() - t0))
    print("| %s | %s | %s |" % (name, n, " ; ".join(out)), flush=True)
for name, args in CURLS:
    n = hello_len_for(lambda port, a=args: run_curl(a, port))
    out = [run_curl(args) for _ in range(TRIES)]
    print("| %s | %s | %s |" % (name, n, " ; ".join(out)), flush=True)
