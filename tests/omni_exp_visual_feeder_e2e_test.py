#!/usr/bin/env python3
"""Live E2E: local screen → feeder → omni-exp agent → Qwen vision answer.

Renders a distinctive code in a local browser page, captures the real screen
via screen-capture-server, feeds it through the omni-exp WS protocol, asks a
spoken question (macOS `say`), and asserts Qwen reads the code back.

DashScope vision quirk (discovered empirically, 2026-08-20): an image frame is
only retained for a turn when it arrives DURING an active VAD utterance —
text-only prompt turns do not see buffered images. ask_about_screen()
interleaves the frame mid-question to match the production 1Hz client ticker.

Requirements (skips cleanly when missing):
- omni-exp agent on :7090
- screen-capture-server on :7900 (+ token)
- DASHSCOPE upstream reachable
- macOS `say` + `ffmpeg` for the spoken question

Run:
    .venv/bin/python3 tests/omni-exp-visual-feeder-e2e.test.py
"""

import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests" / "visual_feeder"))

CODE_WORD = "ZEBRA"
CODE_NUM = "742"

PAGE = f"""<!DOCTYPE html>
<html><head><title>Sutando Vision Test</title><style>
body{{background:#0f3460;color:#fff;font-family:Helvetica;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0}}
h1{{font-size:72px}}.code{{font-size:120px;color:#e94560;font-weight:bold;letter-spacing:12px}}
</style></head><body>
<h1>SUTANDO VISION TEST</h1>
<div class="code">{CODE_WORD}-{CODE_NUM}</div>
<p style="font-size:32px">If you can read this, report the code above.</p>
</body></html>"""

QUESTION = ("Look at my screen. What is the animal word and the number "
            "in the big colored text?")


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("localhost", port)) == 0


def _spoken_question_pcm() -> bytes:
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "q.aiff"
        pcm = Path(td) / "q.pcm"
        subprocess.run(["say", "-o", str(aiff), QUESTION], check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1",
             "-f", "s16le", str(pcm)],
            check=True, capture_output=True,
        )
        return pcm.read_bytes()


async def main() -> int:
    from omni_visual_test_feeder import OmniVisualTestFeeder, read_capture_token
    import aiohttp

    if not _port_open(7090):
        print("SKIP: omni-exp agent not running on :7090")
        return 0
    if not _port_open(7900) or not read_capture_token():
        print("SKIP: screen-capture-server not running on :7900")
        return 0

    page = Path(tempfile.gettempdir()) / "sutando-vision-test.html"
    page.write_text(PAGE)
    subprocess.run(["open", str(page)], check=True)
    await asyncio.sleep(2)  # let the browser render
    # Raise the page's browser window. `open` alone does not always switch
    # macOS Spaces — the frame then captures the wallpaper and Qwen describes
    # scenery. Try to activate a known browser, then VERIFY it is frontmost;
    # if the environment refuses (fullscreen Space, user interacting), SKIP
    # rather than fail — the assertion would be about the desktop, not the
    # vision pipeline.
    running = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to name of every process '
         'whose background only is false'],
        capture_output=True, text=True,
    ).stdout
    browser = next(
        (c for c in ("Google Chrome", "Safari", "Arc", "Firefox") if c in running),
        None,
    )
    frontmost = ""
    for _ in range(3):
        if browser:
            subprocess.run(
                ["osascript", "-e", f'tell application "{browser}" to activate'],
                capture_output=True,
            )
        else:
            subprocess.run(["open", str(page)], check=True)
        await asyncio.sleep(2)
        frontmost = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to name of first process '
             'whose frontmost is true'],
            capture_output=True, text=True,
        ).stdout.strip()
        if browser and frontmost == browser:
            break
    if browser and frontmost != browser:
        print(f"SKIP: could not bring {browser} frontmost (frontmost={frontmost}) — "
              "screen would show the wrong content")
        return 0

    pcm = _spoken_question_pcm()

    feeder = OmniVisualTestFeeder(mode="screen")
    try:
        await feeder.connect()
        print("handshake OK (session.ready received)")
        # read replies ourselves
        if feeder._reader_task:
            feeder._reader_task.cancel()
            try:
                await feeder._reader_task
            except asyncio.CancelledError:
                pass
        ws = feeder._ws

        frame = await feeder._capture_real_screenshot()
        print(f"screen frame: {len(frame)}B")
        assert await feeder.ask_about_screen(pcm, frame), "ask_about_screen failed"

        deadline = time.time() + 60
        answer = ""
        while time.time() < deadline:
            try:
                msg = await ws.receive(timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            d = json.loads(msg.data)
            if d.get("type") == "transcript" and d.get("role") == "assistant":
                answer = str(d.get("text") or "")
                if d.get("final"):
                    break
            elif d.get("type") == "error":
                print("agent error:", d.get("message"))

        print("ANSWER:", answer[:300])
        up = answer.upper()
        if CODE_WORD in up and CODE_NUM in up:
            print("PASS — Qwen read the on-screen code through the full stack")
            return 0
        print("FAIL — code not read (is the test page frontmost on the display?)")
        return 1
    finally:
        await feeder.stop()


@pytest.mark.asyncio
async def test_omni_exp_visual_feeder_e2e():
    """Pytest wrapper for the E2E test."""
    result = await main()
    if result != 0:
        raise AssertionError("E2E test failed")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
