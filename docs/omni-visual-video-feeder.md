# Omni Visual Video Feeder

Feeds real, changing video content — a podcast, a screen recording, a YouTube
URL, or a live LiveKit track — to the omni agent as visual test input.

Synthetic colour-block patterns (the other feeder modes) are fine for checking
"frames flow and the agent sees *something*", but they don't exercise the
behaviour that matters for realistic content: scene-change detection, board-ink
OCR, and "describe what you see" all respond differently to a talking head,
a slideshow, or handwriting than to a static test pattern.

## Why video instead of synthetic frames

- **Scene change** — the omni agent gates vision uploads on a mean-abs-diff
  threshold (`SCENE_THRESHOLD`). A static pattern never crosses it; a video
  crosses it naturally, so the scene-triggered prompt path actually runs.
- **Content realism** — OCR and "what do you see" tests need real text and
  real motion, not colour blocks.
- **Reproducibility** — the same video feeds the same frames on every run, so
  a vision regression is bisectable.

## Sources

Three sources, one output format. Every source yields JPEG bytes, and delivery
reuses `OmniVisualTestFeeder.inject_single_frame`, so the wire protocol has
exactly one implementation.

| Source | Class | Depends on |
|--------|-------|-----------|
| Local file | `FileVideoSource` | ffmpeg |
| Remote URL / YouTube / podcast | `UrlVideoSource` | ffmpeg + yt-dlp |
| LiveKit room track | `LiveKitVideoSource` | `livekit` + Pillow |

### 1. File

```python
from video_source_feeder import FileVideoSource, VideoFeeder

async with VideoFeeder(FileVideoSource("podcast.mp4", fps=0.2)) as feeder:
    await feeder.wait_frames(10)
print(feeder.stats)
```

### 2. URL (YouTube / podcast)

```python
from video_source_feeder import UrlVideoSource, VideoFeeder

src = UrlVideoSource("https://www.youtube.com/watch?v=...", fps=0.2)
async with VideoFeeder(src) as feeder:
    await feeder.run_for(60)  # feed for one minute
```

`yt-dlp` resolves the page URL to a direct media URL; nothing is downloaded to
disk, frames stream straight through ffmpeg. A direct media URL (`.mp4`,
`.m3u8`, …) skips yt-dlp entirely.

### 3. LiveKit room

```python
from video_source_feeder import LiveKitVideoSource, VideoFeeder

src = LiveKitVideoSource(room="sutando-test", fps=0.5)
async with VideoFeeder(src) as feeder:
    await feeder.wait_frames(5)
```

Joins the room as a subscriber-only participant and feeds whatever video track
any participant publishes — a browser tab, a phone camera, a screen share.
Credentials come from `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`
(the same env vars `src/livekit-agent.py` uses).

## Frame rate and pacing

- `fps` decimates in ffmpeg (`-vf fps=`), so only the frames actually wanted
  are ever decoded — feeding a one-hour podcast at 0.2 fps costs far less than
  decoding every frame and dropping most of them in Python.
- `realtime=True` adds ffmpeg's `-re` flag, so a 30-minute video feeds over
  30 minutes. Without it ffmpeg races through the file and the agent sees the
  whole thing in seconds.
- `start_at` seeks before feeding (`"00:05:00"`), `duration_s` stops after a
  number of seconds of video.

## CLI

```bash
# Feed a local file, 1 frame every 5 seconds, stop after 60 frames
.venv/bin/python3 tests/visual_feeder/video_source_feeder.py \
    --file podcast.mp4 --fps 0.2 --frames 60

# Feed a YouTube podcast, real-time paced, for 2 minutes
.venv/bin/python3 tests/visual_feeder/video_source_feeder.py \
    --url "https://www.youtube.com/watch?v=..." --fps 0.2 \
    --realtime --duration 120

# Feed whatever video is published into a LiveKit room
.venv/bin/python3 tests/visual_feeder/video_source_feeder.py \
    --livekit-room sutando-test --fps 0.5 --frames 10
```

## How it works

```
FileVideoSource ─┐
UrlVideoSource  ─┼─ ffmpeg (-vf fps,scale / -vcodec mjpeg) ── JPEG bytes ──┐
LiveKitSource   ─┘  (VideoFrame → RGB24 → JPEG)                           │
                                                                          ▼
                                              OmniVisualTestFeeder.inject_single_frame
                                                                          │
                                                    {"type":"image","data":"<b64>"}
                                                                          ▼
                                                               omni-exp agent (:7090/ws)
```

- ffmpeg outputs `image2pipe`/`mjpeg`: a bare concatenation of JPEGs with no
  length prefix. Frames are split on the `\xff\xd8` … `\xff\xd9` markers,
  retaining any partial frame across reads.
- The read buffer is capped at 32 MB — a wrong ffmpeg command or an HTML error
  page fails loudly instead of growing until OOM.
- LiveKit frames are `VideoFrame.convert(RGB24)` → Pillow → JPEG. Pillow is
  only required for the LiveKit source, not for file/URL.

## Wire protocol

Delivery matches `src/omni-exp-agent.py` exactly (reusing the existing feeder):
- **TEXT** JSON frames only — binary is rejected by the agent.
- Mandatory `{"type":"session.start","user":…,"auth":…}` handshake first.
- Images are `{"type":"image","mime":"image/jpeg","data":"<base64>"}`.
- Server-side rejections are counted in `stats.errors_received`, not swallowed.

## Tests

`tests/omni-visual-video-feeder.test.py` covers the JPEG splitter (pure), the
ffmpeg decode path (synthesizes a clip), and a live feed to the agent (skips
cleanly when the agent isn't running).

```bash
.venv/bin/python3 tests/omni-visual-video-feeder.test.py
```

## Finding a good test video

Any video with real motion and text works. For a "podcast" feel, a YouTube
interview or talk is ideal — a talking head triggers scene changes, and any
on-screen slides/chyrons give the OCR path something to read. Prefer:

- ≤ 720p (smaller frames, faster decode, same visual fidelity for the agent)
- a source you can re-link or pin (reproducible test input)
- anything with spoken content if you'll pair frames with the audio-inject
  path for a full multimodal test
