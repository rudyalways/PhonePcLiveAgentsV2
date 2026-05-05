#!/usr/bin/env python3
"""
LiveKit Token Server for Sutando — generates JWT tokens for Room participants.
Runs locally on port 7850. Requires user authentication via ?user= and ?secret=.

Usage: python3 src/livekit-token-server.py
Requires: pip install livekit-api python-dotenv
"""

import asyncio
import hashlib
import json
import os
import threading
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

from livekit.api import (
    AccessToken,
    VideoGrants,
    LiveKitAPI,
    CreateAgentDispatchRequest,
    DeleteAgentDispatchRequest,
    ListParticipantsRequest,
)

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
PORT = int(os.environ.get("TOKEN_SERVER_PORT", "7850"))

USERS_FILE = Path(__file__).resolve().parent / "users.json"

if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
    print("Error: LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set in .env")
    exit(1)


def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    data = json.loads(USERS_FILE.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def verify_user(username: str, secret: str) -> dict | None:
    """Return user info dict if credentials valid, else None."""
    users = load_users()
    user = users.get(username)
    if not user:
        return None
    expected = user.get("secret_sha256", "")
    actual = hashlib.sha256(secret.encode()).hexdigest()
    if actual != expected:
        return None
    return user


def create_token(identity: str, name: str, room: str) -> str:
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name or identity)
        .with_ttl(timedelta(hours=24))
        .with_grants(VideoGrants(
            room_join=True,
            room=room,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
    )
    return token.to_jwt()


async def _ensure_agent_dispatched(room: str) -> None:
    """Dispatch the sutando agent if no agent participant is currently active in the room."""
    if not LIVEKIT_URL:
        return
    async with LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as lkapi:
        # Check for a live agent participant (kind=4) — stale dispatch records don't count
        try:
            parts = await lkapi.room.list_participants(ListParticipantsRequest(room=room))
            for p in parts.participants:
                if p.kind == 4:  # ParticipantInfo.Kind.AGENT
                    print(f"[TokenServer] Agent already in room {room}", flush=True)
                    return
        except Exception:
            pass  # Room doesn't exist yet — proceed to dispatch

        # Clean up stale dispatches before creating a fresh one
        dispatches = await lkapi.agent_dispatch.list_dispatch(room)
        for d in dispatches:
            if d.agent_name == "sutando":
                try:
                    await lkapi.agent_dispatch.delete_dispatch(
                        DeleteAgentDispatchRequest(dispatch_id=d.id, room=room)
                    )
                except Exception:
                    pass

        await lkapi.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(room=room, agent_name="sutando")
        )
        print(f"[TokenServer] Dispatched agent to {room}", flush=True)


def ensure_agent_dispatched(room: str) -> None:
    try:
        asyncio.run(_ensure_agent_dispatched(room))
    except Exception as e:
        print(f"[TokenServer] Agent dispatch error: {e}", flush=True)


class TokenHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/token", "/api/token"):
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        username = params.get("user", [""])[0]
        secret = params.get("secret", [""])[0]
        identity = params.get("identity", ["phone-user"])[0]
        name = params.get("name", [identity])[0]

        if not username or not secret:
            self._error(401, "Missing user or secret")
            return

        user_info = verify_user(username, secret)
        if not user_info:
            self._error(401, "Invalid credentials")
            return

        room = user_info["room"]

        try:
            jwt = create_token(identity, name, room)

            body = json.dumps({
                "jwt": jwt,
                "room": room,
                "url": LIVEKIT_URL,
                "identity": identity,
                "username": username,
            }).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

            # Dispatch agent in background so the response is sent immediately
            if identity == "phone-user":
                threading.Thread(
                    target=ensure_agent_dispatched,
                    args=(room,),
                    daemon=True,
                ).start()
        except Exception as e:
            self._error(500, str(e))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _error(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[TokenServer] {args[0]}", flush=True)


def main():
    server = HTTPServer(("0.0.0.0", PORT), TokenHandler)
    users = load_users()

    print(f"LiveKit Token Server running on http://0.0.0.0:{PORT}", flush=True)
    print(f"  LiveKit URL: {LIVEKIT_URL or '(not set)'}", flush=True)
    print(f"  Users loaded: {len(users)} ({', '.join(users.keys()) or 'none'})", flush=True)
    if not users:
        print("  ⚠ No users configured. Run: python3 src/add-user.py <username> <secret>", flush=True)

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
