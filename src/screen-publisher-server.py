#!/usr/bin/env python3
"""
Minimal HTTPS server to serve screen-publisher.html and mobile-client.html.
HTTPS is required for WebRTC (getUserMedia) on non-localhost origins.

Usage: python3 src/screen-publisher-server.py
"""

import os
import ssl
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PORT = int(os.environ.get("CLIENT_PORT", "7081"))
TOKEN_PORT = int(os.environ.get("TOKEN_SERVER_PORT", "7850"))
SRC_DIR = Path(__file__).resolve().parent
STATE_DIR = SRC_DIR.parent / "state"
CERT_FILE = STATE_DIR / "server.crt"
KEY_FILE = STATE_DIR / "server.key"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path_only = parsed.path or "/"
        query_suffix = ("?" + parsed.query) if parsed.query else ""

        if path_only.startswith("/token") or path_only.startswith("/room-state"):
            return self._proxy_token()

        if path_only in ("/", "/screen", "/index.html"):
            self.path = "/screen-publisher.html" + query_suffix
        elif path_only in ("/mobile", "/mobile.html", "/phone"):
            # Map short paths to mobile-client.html but keep ?query (e.g. ?micMeter=1).
            self.path = "/mobile-client.html" + query_suffix
        elif path_only in ("/omni", "/omni.html", "/omni-client.html"):
            # Legacy name — send browsers to the live omni-exp agent (HTTPS :7090).
            self.send_response(302)
            self.send_header("Location", "https://127.0.0.1:7090/omni-exp" + query_suffix)
            self.end_headers()
            return
        elif path_only in ("/omni-exp", "/omni-exp.html"):
            # Static copy only — live WSS is on omni-exp-agent (OMNI_EXP_PORT, default 7090).
            self.path = "/omni-exp-client.html" + query_suffix
        else:
            self.path = path_only + query_suffix

        self.directory = str(SRC_DIR)
        return super().do_GET()

    def _proxy_token(self):
        url = f"http://127.0.0.1:{TOKEN_PORT}{self.path}"
        try:
            resp = urlopen(Request(url), timeout=15)
            body = resp.read()
            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = f'{{"error":"token server unreachable: {e}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format, *args):
        print(f"[ScreenServer] {args[0]}", flush=True)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)

    if CERT_FILE.exists() and KEY_FILE.exists():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        proto = "https"
    else:
        proto = "http"
        print("  ⚠ No TLS cert found — running plain HTTP (getUserMedia won't work on mobile)")
        print(f"  Generate cert: openssl req -x509 -newkey rsa:2048 -keyout {KEY_FILE} -out {CERT_FILE} -days 365 -nodes -subj /CN=sutando-local")

    print(f"Screen publisher serving at {proto}://localhost:{PORT}", flush=True)
    print(f"  PC:     {proto}://localhost:{PORT}/", flush=True)
    print(f"  Mobile: {proto}://<your-ip>:{PORT}/mobile", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
