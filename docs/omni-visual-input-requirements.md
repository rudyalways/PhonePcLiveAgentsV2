# Omni Agent Visual Input Requirements

Research findings on frame rate requirements and optimal capture solutions for feeding visual input to the omni agent.

## Frame Rate Requirements

Based on analysis of the omni-exp implementation (`src/omni-exp-agent.py`, `src/omni_exp_provider_qwen.py`):

**No minimum FPS requirement** — the omni agent and Qwen Omni model have no hard frame rate constraints.

### Current Defaults
- `UPLOAD_KEEPALIVE_S = 8` — forces frame upload every 8 seconds minimum
- `UPLOAD_DEDUPE_THRESHOLD` — skips near-identical frames based on mean absolute difference
- `SCENE_THRESHOLD = 28` — only triggers processing on significant visual changes

### Flexible Frame Rates

**Both 2s and 5s intervals work perfectly:**

```bash
# Fast response (0.5 fps)
OMNI_EXP_UPLOAD_KEEPALIVE_S=2

# Balanced (0.2 fps) 
OMNI_EXP_UPLOAD_KEEPALIVE_S=5

# Minimal cost (0.067 fps)
OMNI_EXP_UPLOAD_KEEPALIVE_S=15
```

### Qwen Omni Provider Details

Images flow through `append_image()` which:
- Automatically pads ~100ms silence before each image (Qwen requirement)
- Serializes uploads via lock to prevent interleaving
- No FPS restrictions from the model

## Recommended Test Configurations

### High Responsiveness (2s intervals)
```python
feeder = OmniVisualTestFeeder(mode='synthetic', interval_s=2.0)
```
- 0.5 fps, ~30 frames/minute
- Good for visual change detection tests
- Cost: ~3MB/minute

### Balanced (5s intervals) — Recommended
```python
feeder = OmniVisualTestFeeder(mode='synthetic', interval_s=5.0)
```
- 0.2 fps, 12 frames/minute
- Recommended for most tests
- Minimal system impact

### Minimal Cost (15-30s intervals)
```python
feeder = OmniVisualTestFeeder(mode='synthetic', interval_s=15.0)
```
- 0.033-0.067 fps, 2-4 frames/minute
- Best for long-running tests
- Negligible cost

## Better Capture Solutions

### Apple ScreenCaptureKit (Recommended)

**Official framework** introduced in macOS 12.3+ for high-performance screen capture.

**Advantages over `screencapture` CLI:**
- **Hardware-accelerated** — uses GPU efficiently
- **Direct memory streaming** — no disk I/O
- **Low CPU usage** — 2-5% at 1-30 fps (vs 10-15% with CLI)
- **Battery-friendly** — optimized for continuous operation
- **Fine-grained control** — capture specific windows/apps/displays
- **HDR support** (macOS 14+)

**Performance:**
- Supports up to 60 fps continuous capture
- ~2-5% CPU for 30 fps vs ~10-15% for repeated `screencapture` commands
- Zero disk I/O (streams to memory buffers)

**Implementation examples:**
- [Apple's official sample (Swift)](https://github.com/Fidetro/CapturingScreenContentInMacOS)
- [Rust bindings](https://github.com/doom-fish/screencapturekit-rs)
- [Python example achieving 30 fps on M2](https://gist.github.com/mr-linch/d31024f931441a39c6a830328f8b5030)

### Comparison

| Method | CPU (1 fps) | Disk I/O | Streaming | Best Use |
|--------|-------------|----------|-----------|----------|
| `screencapture` CLI | 10-15% | Yes (file per frame) | No | Quick captures |
| ScreenCaptureKit | 2-5% | None | Yes | Continuous capture |

### When to Upgrade

**Current `screencapture` approach is fine for:**
- Test scenarios at 2-5s intervals
- Synthetic test patterns (zero capture cost)
- Occasional screenshots

**Consider ScreenCaptureKit if:**
- Need >1 fps sustained capture
- Want to eliminate disk I/O completely
- Building production visual streaming feature
- Battery life is critical

### Upgrade Path

1. Replace `src/screen-capture-server.py` with ScreenCaptureKit-based implementation
2. Use Apple's sample code as reference
3. Stream frames directly to WebSocket (eliminate temp files)
4. Keep existing HTTP API for backward compatibility

## Cost Analysis

### Synthetic Mode (Zero Capture Cost)
- CPU: Negligible (PIL generates in-memory)
- Disk: Zero
- Energy: Minimal
- Network: ~100KB/frame to omni WebSocket

### Screen Capture Mode
| Interval | Frames/min | Data/min | CPU | Energy |
|----------|------------|----------|-----|--------|
| 2s | 30 | ~3MB | Low | Low |
| 5s | 12 | ~1.2MB | Minimal | Minimal |
| 15s | 4 | ~400KB | Negligible | Negligible |

## Sources

- [Meet ScreenCaptureKit (WWDC 2022)](https://developer.apple.com/videos/play/wwdc2022/10156/)
- [Take ScreenCaptureKit to the next level (WWDC 2022)](https://developer.apple.com/videos/play/wwdc2022/10155/)
- [What's new in ScreenCaptureKit (WWDC 2023)](https://developer.apple.com/videos/play/wwdc2023/10136/)
- [Capture HDR content with ScreenCaptureKit (WWDC 2024)](https://developer.apple.com/videos/play/wwdc2024/10088/)
- [Apple sample code on GitHub](https://github.com/Fidetro/CapturingScreenContentInMacOS)
- [screencapturekit-rs (Rust bindings)](https://github.com/doom-fish/screencapturekit-rs)
- [30 fps Python example](https://gist.github.com/mr-linch/d31024f931441a39c6a830328f8b5030)
- [efficient-recorder (battery-optimized streaming)](https://github.com/janwilmake/efficient-recorder)

## See Also

- `docs/omni-visual-test-framework.md` — Test framework documentation
- `tests/visual_feeder/omni_visual_test_feeder.py` — Test feeder implementation
- `src/vision-tools.ts` — Production visual input pipeline
- `src/omni-exp-agent.py` — Omni agent implementation
