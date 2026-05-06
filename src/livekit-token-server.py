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
    ListParticipantsRequest,
    RoomParticipantIdentity,
)

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
PORT = int(os.environ.get("TOKEN_SERVER_PORT", "7850"))

_SERVER_START_TIME = int(time.time())

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


HEARTBEAT_FILE = Path(__file__).resolve().parent.parent / "state" / "livekit-agent.heartbeat"


async def _ensure_agent_dispatched(room: str) -> None:
    """Dispatch the sutando agent if no live agent participant is in the room."""
    if not LIVEKIT_URL:
        return
    async with LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as lkapi:
        # Remove any stale agent participants from previous sessions
        try:
            parts = await lkapi.room.list_participants(ListParticipantsRequest(room=room))
            for p in parts.participants:
                if p.kind == 4:  # ParticipantInfo.Kind.AGENT
                    if p.joined_at < _SERVER_START_TIME:
                        print(f"[TokenServer] Removing stale agent {p.identity}", flush=True)
                        try:
                            await lkapi.room.remove_participant(
                                RoomParticipantIdentity(room=room, identity=p.identity)
                            )
                        except Exception as e:
                            print(f"[TokenServer] Failed to remove stale agent: {e}", flush=True)
                    else:
                        print(f"[TokenServer] Agent already in room {room}", flush=True)
                        return
        except Exception:
            pass  # Room doesn't exist yet — proceed to dispatch

        # Delete ALL existing dispatches (including empty-agent_name ones left by ghost workers)
        dispatches = await lkapi.agent_dispatch.list_dispatch(room)
        for d in dispatches:
            try:
                await lkapi.agent_dispatch.delete_dispatch(d.id, room)
            except Exception:
                pass

        dispatch_time = int(time.time())
        new_dispatch = await lkapi.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(room=room, agent_name="sutando")
        )
        print(f"[TokenServer] Dispatched agent to {room} (dispatch={new_dispatch.id})", flush=True)

        # Poll up to 15s for our local worker to accept the job.
        # We verify via the heartbeat file: our worker writes it on every entrypoint entry.
        # A ghost worker would update the room participant list but NOT our heartbeat file.
        for attempt in range(15):
            await asyncio.sleep(1)
            try:
                parts2 = await lkapi.room.list_participants(ListParticipantsRequest(room=room))
                for p in parts2.participants:
                    if p.kind == 4 and p.joined_at >= dispatch_time:
                        # Agent joined — check heartbeat to confirm it's our local worker
                        try:
                            hb = int(HEARTBEAT_FILE.read_text().strip())
                            if hb >= dispatch_time:
                                print(f"[TokenServer] Agent confirmed (local worker): {p.identity} t+{attempt+1}s", flush=True)
                                return
                        except Exception:
                            pass
                        # Agent joined but heartbeat not updated yet — wait a bit more
                        if attempt >= 7:
                            print(f"[TokenServer] Warning: agent {p.identity} joined but heartbeat not updated — possible ghost worker", flush=True)
                            await lkapi.room.remove_participant(RoomParticipantIdentity(room=room, identity=p.identity))
                            break  # Re-dispatch not implemented; log the warning
            except Exception:
                pass

        # If we get here, agent either never joined or was a ghost worker
        try:
            hb = int(HEARTBEAT_FILE.read_text().strip())
            if hb >= dispatch_time:
                print(f"[TokenServer] Agent confirmed via heartbeat (late)", flush=True)
                return
        except Exception:
            pass
        print(f"[TokenServer] Warning: no confirmed local agent after 15s for {room}", flush=True)


def ensure_agent_dispatched(room: str) -> None:
    try:
        asyncio.run(_ensure_agent_dispatched(room))
    except Exception as e:
        print(f"[TokenServer] Agent dispatch error: {e}", flush=True)


async def _startup_cleanup() -> None:
    """Remove stale agents and dispatches from all sutando rooms on server start."""
    if not LIVEKIT_URL:
        return
    try:
        from livekit.api import ListRoomsRequest
        async with LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as lkapi:
            rooms = await lkapi.room.list_rooms(ListRoomsRequest())
            for r in rooms.rooms:
                if not r.name.startswith("sutando-"):
                    continue
                # Remove stale agent participants
                parts = await lkapi.room.list_participants(ListParticipantsRequest(room=r.name))
                for p in parts.participants:
                    if p.kind == 4:  # AGENT
                        print(f"[TokenServer] Startup: removing stale agent {p.identity} from {r.name}", flush=True)
                        try:
                            await lkapi.room.remove_participant(
                                RoomParticipantIdentity(room=r.name, identity=p.identity)
                            )
                        except Exception as e:
                            print(f"[TokenServer] Startup: failed to remove {p.identity}: {e}", flush=True)
                # Delete all stale dispatches (any agent_name)
                dispatches = await lkapi.agent_dispatch.list_dispatch(r.name)
                for d in dispatches:
                    print(f"[TokenServer] Startup: deleting stale dispatch {d.id} (agent={repr(d.agent_name)}) from {r.name}", flush=True)
                    try:
                        await lkapi.agent_dispatch.delete_dispatch(d.id, r.name)
                    except Exception as e:
                        print(f"[TokenServer] Startup: failed to delete dispatch {d.id}: {e}", flush=True)
    except Exception as e:
        print(f"[TokenServer] Startup cleanup error: {e}", flush=True)


def startup_cleanup() -> None:
    try:
        asyncio.run(_startup_cleanup())
    except Exception as e:
        print(f"[TokenServer] Startup cleanup error: {e}", flush=True)


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

    # Clean up stale agent participants from previous sessions
    startup_cleanup()

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
