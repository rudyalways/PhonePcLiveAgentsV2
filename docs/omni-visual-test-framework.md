# Omni Visual Test Framework

Test framework component for feeding visual input to the omni agent. Designed for controlled testing of omni agent visual processing capabilities with minimal system cost.

## Overview

The `OmniVisualTestFeeder` provides:
- **Synthetic test pattern generation** — no screen capture needed, minimal CPU/energy cost
- **Real screenshot capture** — via screen-capture-server for integration tests
- **ScreenCaptureKit capture** — high-performance native capture (macOS 12.3+)
- **Configurable frame rate** — default 10s interval for cost-effective testing
- **Test control primitives** — wait for N frames, inject single frames, collect statistics

## Components

### 1. Python-based Feeder (Cross-platform)
**File**: `tests/visual_feeder/omni_visual_test_feeder.py`

- Synthetic test patterns or screen-capture-server integration
- Works on any macOS version
- Lower performance but simpler setup

### 2. ScreenCaptureKit Feeder (macOS 12.3+)
**Files**: 
- `tests/visual_feeder/screencapturekit-feeder.swift` — Swift implementation
- `tests/visual_feeder/screencapturekit_feeder_wrapper.py` — Python wrapper

- Hardware-accelerated capture
- 2-5% CPU vs 10-15% for CLI approach
- Zero disk I/O (direct memory streaming)
- **Best for performance-critical tests**

### 3. Example Tests
**File**: `tests/omni-visual-test-feeder-example.test.py`

Demonstrates usage patterns for both feeders.

## Location

- **Implementation**: `tests/visual_feeder/omni_visual_test_feeder.py`
- **Example tests**: `tests/omni-visual-test-feeder-example.test.py`
- **Documentation**: this file

## Why a Separate Component?

Production visual input (`src/vision-tools.ts`, `src/omni-exp-agent.py`) is optimized for real-time user interaction with scene detection, deduplication, and keepalive logic. Test scenarios need:
- **Deterministic frame injection** — not scene-triggered
- **Very low frame rates** — 1 frame per 10-30 seconds for cost-effective testing
- **Synthetic frames** — reproducible test patterns without screen capture
- **Test control** — wait primitives, statistics, manual injection

## Performance Comparison

| Implementation | CPU (0.2 fps) | Disk I/O | Memory | Best For |
|----------------|---------------|----------|---------|----------|
| Synthetic (Python) | Negligible | None | Low | Unit tests, quick validation |
| screencapture CLI | ~10-15% | High (temp files) | Low | Simple integration tests |
| ScreenCaptureKit | ~2-5% | None | Medium | Performance tests, sustained capture |

**ScreenCaptureKit advantages:**
- Hardware-accelerated (GPU)
- Direct memory streaming
- Can sustain higher frame rates with lower CPU
- Native macOS framework (official Apple API)

**When to use each:**
- **Synthetic**: Default for most tests, zero capture cost
- **screencapture CLI**: Simple integration tests where performance doesn't matter
- **ScreenCaptureKit**: Performance-critical tests, sustained capture >1 fps, battery testing
```python
feeder = OmniVisualTestFeeder(mode='synthetic', interval_s=10)
```
- **CPU**: Negligible (PIL generates test patterns in-memory)
- **Disk**: Zero (no files written)
- **Energy**: Minimal (comparable to idle)
- **Network**: ~100KB per frame to omni-exp WebSocket

### Screen Capture Mode
```python
feeder = OmniVisualTestFeeder(mode='screen', interval_s=15)
```
- **CPU**: Low (native macOS screencapture, efficient)
- **Disk**: Transient (files deleted immediately after read)
- **Energy**: Low (comparable to video call in background)
- **Network**: ~80-150KB per frame

At 1 frame every 10-15 seconds, system impact is negligible even for extended test runs.

## Usage

### ScreenCaptureKit Feeder (Recommended for macOS 12.3+)

**Python wrapper:**
```python
from screencapturekit_feeder_wrapper import ScreenCaptureKitFeeder

async def test_with_screencapturekit():
    feeder = ScreenCaptureKitFeeder(fps=0.2, omni_port=7090)
    
    await feeder.start()
    await asyncio.sleep(30)  # Let it capture
    stats = await feeder.stop()
    
    print(stats)  # FeederStats(frames=6, failed=0, duration=30.0s, fps=0.200)
```

**Direct Swift:**
```bash
# 1 frame every 5 seconds
swift tests/visual_feeder/screencapturekit-feeder.swift --fps 0.2 --port 7090

# Faster: 1 frame every 2 seconds
swift tests/visual_feeder/screencapturekit-feeder.swift --fps 0.5
```

### Python Feeder (Any macOS version)

**Basic Synthetic Test:**
```python
from omni_visual_test_feeder import OmniVisualTestFeeder

async def test_omni_visual_response():
    feeder = OmniVisualTestFeeder(
        mode='synthetic',
        interval_s=10.0,
        omni_port=7090,
    )
    
    await feeder.start()
    await feeder.wait_frames(5)  # Wait for 5 frames
    stats = await feeder.stop()
    
    assert stats.frames_sent >= 5
    assert stats.frames_failed == 0
```

### Manual Frame Injection

```python
from omni_visual_test_feeder import OmniVisualTestFeeder, create_test_pattern

feeder = OmniVisualTestFeeder(mode='synthetic')

# Create custom test patterns
error_frame = create_test_pattern("Error State", color='#e94560')
normal_frame = create_test_pattern("Normal", color='#16213e')

# Inject specific frames at test-controlled times
await feeder.inject_single_frame(error_frame)
await asyncio.sleep(5)
await feeder.inject_single_frame(normal_frame)
```

### Real Screenshot Test

```python
feeder = OmniVisualTestFeeder(
    mode='screen',
    interval_s=15.0,
    screen_capture_port=7900,  # Requires screen-capture-server running
)

await feeder.start()
await feeder.wait_frames(3)
stats = await feeder.stop()
```

## Test Scenarios

### 1. Visual Context Processing
Test that omni agent incorporates visual context into responses:
- Inject synthetic frame with clear text pattern
- Send voice/text query asking about the displayed content
- Assert response includes visual context

### 2. Frame Rate Handling
Test omni agent behavior at different frame rates:
- Very low: 1 frame per 30s (minimal cost)
- Low: 1 frame per 10s (recommended default)
- Moderate: 1 frame per 5s (integration tests)

### 3. Frame Content Variation
Test omni agent's ability to detect visual changes:
- Inject sequence: normal → error → normal frames
- Verify omni agent notices the state change

### 4. Concurrent Audio + Visual
Test multimodal input handling:
- Start visual feeder in background
- Send audio/text input via normal test harness
- Verify both modalities processed correctly

## Configuration

Environment variables (optional):
```bash
OMNI_EXP_PORT=7090                    # Omni-exp-agent port
SCREEN_CAPTURE_PORT=7900              # Screen capture server port
OMNI_VISUAL_TEST_INTERVAL=10          # Default frame interval (seconds)
```

## Dependencies

Required:
- `aiohttp` — WebSocket connection to omni-exp-agent

Optional:
- `Pillow` (PIL) — synthetic test pattern generation (falls back to minimal JPEG if unavailable)

Install:
```bash
pip install aiohttp pillow
```

## Statistics

The feeder tracks:
- `frames_sent` — successfully injected frames
- `frames_failed` — injection failures
- `duration_s` — test duration
- `avg_fps` — average frame rate

Access via `stats` after `stop()`:
```python
stats = await feeder.stop()
print(f"Sent {stats.frames_sent} frames in {stats.duration_s:.1f}s")
print(f"Average FPS: {stats.avg_fps:.3f}")
```

## Integration with Existing Tests

The feeder is a standalone component that complements existing omni-exp tests:
- **`tests/omni-exp-*.test.py`** — test omni agent logic, mode transitions, task handling
- **This framework** — test visual input processing specifically

Use together:
```python
# Start visual feeder in background
feeder = OmniVisualTestFeeder(mode='synthetic', interval_s=10)
await feeder.start()

# Run existing omni-exp test logic
# ... your test code here ...

# Stop feeder and check stats
stats = await feeder.stop()
assert stats.frames_sent > 0  # Verify visual input was provided
```

## Architecture

```
Test Script
    ↓
OmniVisualTestFeeder
    ↓ (WebSocket)
omni-exp-agent.py (:7090/ws)
    ↓
Qwen Omni Realtime API
```

The feeder connects directly to omni-exp's WebSocket endpoint, mimicking the phone client but with test-controlled frame timing and content.

## Limitations

1. **WebSocket only** — connects to omni-exp WebSocket, not the Qwen provider directly
2. **No audio** — visual frames only (use existing test harness for audio)
3. **Test TLS** — uses `ssl=False` for local testing (omni-exp uses self-signed cert)
4. **Single session** — one feeder per omni-exp instance

## Future Enhancements

- [ ] Audio + visual synchronized injection
- [ ] Frame sequence from directory (for replay tests)
- [ ] OCR validation on synthetic frames
- [ ] Latency measurement (frame sent → omni response)
- [ ] Multi-session support (concurrent feeders)

## See Also

- `src/vision-tools.ts` — production visual input pipeline
- `src/omni-exp-agent.py` — omni agent implementation
- `tests/omni-exp-*.test.py` — existing omni-exp tests
- `docs/built-in-tools.md` — screen capture API reference
