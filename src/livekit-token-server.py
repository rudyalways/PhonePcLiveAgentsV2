#!/usr/bin/env python3
"""
LiveKit Token Server for Sutando — generates JWT tokens for Room participants.
Runs locally on port 7850. Each participant (screen-publisher, agent, phone-user)
gets a token scoped to the same room.

Usage: python3 src/livekit-token-server.py
Requires: pip install livekit-api python-dotenv
"""

import json
import os
import time
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from livekit.api import AccessToken, VideoGrants

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
ROOM_NAME = os.environ.get("LIVEKIT_ROOM", "sutando-room")
PORT = int(os.environ.get("TOKEN_SERVER_PORT", "7850"))

if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
    print("Error: LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set in .env")
    exit(1)


def create_token(identity: str, name: str = "") -> str:
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name or identity)
        .with_ttl(timedelta(hours=24))
        .with_grants(VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
    )
    return token.to_jwt()


class TokenHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/token", "/api/token"):
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        identity = params.get("identity", ["phone-user"])[0]
        name = params.get("name", [identity])[0]

        try:
            jwt = create_token(identity, name)
            body = json.dumps({
                "jwt": jwt,
                "room": ROOM_NAME,
                "url": os.environ.get("LIVEKIT_URL", ""),
                "identity": identity,
            }).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[TokenServer] {args[0]}", flush=True)


def main():
    server = HTTPServer(("0.0.0.0", PORT), TokenHandler)

    print(f"LiveKit Token Server running on http://0.0.0.0:{PORT}", flush=True)
    print(f"  Room: {ROOM_NAME}", flush=True)
    print(f"  LiveKit URL: {os.environ.get('LIVEKIT_URL', '(not set)')}", flush=True)

    state_dir = Path(__file__).resolve().parent.parent / "state"
    state_dir.mkdir(exist_ok=True)
    heartbeat = state_dir / "token-server.heartbeat"

    try:
        heartbeat.write_text(str(int(time.time())))
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nToken server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
