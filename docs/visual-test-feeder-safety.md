# Visual Test Feeder Safety Guide

This document explains how the visual test feeders are designed to prevent accidental background resource waste and protect your Mac hardware.

## Safety Features Built-In

### 1. No Auto-Start on Import
The feeders are **library components**, not daemons:
- Importing the module does nothing
- Must explicitly call `start()` to begin capture
- Will not run unless your test code activates it

### 2. Explicit Process Management
All feeders require manual lifecycle control:
```python
feeder = ScreenCaptureKitFeeder()  # Created but NOT running
await feeder.start()                # Explicitly start
# ... test code ...
await feeder.stop()                 # Explicitly stop
```

### 3. No Persistence
- **Nothing runs after your test exits**
- Process lifetime tied to test process
- No background daemons installed
- No launchd/cron jobs created

### 4. Default Low Frame Rate
- Default: **0.5 fps (1 frame every 2 seconds)**
- Conservative for battery and CPU
- Explicit override required for higher rates

### 5. Subprocess Cleanup
The Python wrapper (`screencapturekit-feeder-wrapper.py`) ensures cleanup:
- Sends SIGINT on stop
- 5-second timeout, then SIGKILL
- Subprocess dies when parent test exits

## How to Verify Nothing is Running

### Check for active feeders:
```bash
# Check for Swift feeder process
ps aux | grep screencapturekit-feeder

# Check for Python feeder
ps aux | grep omni-visual-test-feeder

# Should return nothing if no tests are running
```

### Monitor resource usage:
```bash
# CPU usage
top -o cpu | head -20

# Check if screen-capture-server is idle
lsof -i :7900
```

## Best Practices

### 1. Use try/finally for Cleanup
```python
feeder = OmniVisualTestFeeder(mode='synthetic')
try:
    await feeder.start()
    # ... test code ...
finally:
    await feeder.stop()  # Always runs, even on exception
```

Note: `async with` context manager syntax is not implemented. Always use `try/finally`.

### 3. Set Timeouts on Tests
```python
async def test_visual():
    feeder = ScreenCaptureKitFeeder()
    await feeder.start()
    
    try:
        # Timeout ensures test doesn't run forever
        await asyncio.wait_for(
            feeder.wait_frames(10),
            timeout=60.0
        )
    finally:
        await feeder.stop()
```

### 4. Use Synthetic Mode by Default
```python
# Synthetic = zero screen capture cost
feeder = OmniVisualTestFeeder(mode='synthetic')
```

### 5. Limit Test Duration in CI
```bash
# pytest with timeout
pytest tests/omni-visual-*.test.py --timeout=120

# Or in test code
@pytest.mark.timeout(120)
async def test_omni_visual():
    ...
```

## What NOT to Do

❌ **Don't background the process manually:**
```bash
# BAD: Creates orphan process
swift screencapturekit-feeder.swift &
```

❌ **Don't run without explicit stop:**
```python
# BAD: No cleanup
feeder.start()
# test exits, feeder might keep running
```

❌ **Don't set very high FPS without reason:**
```python
# BAD: Wastes resources for no benefit
feeder = ScreenCaptureKitFeeder(fps=10.0)  # 10 fps is excessive for tests
```

✅ **DO use structured cleanup:**
```python
# GOOD: Explicit lifecycle
async def test():
    feeder = ScreenCaptureKitFeeder()
    await feeder.start()
    try:
        await feeder.wait_frames(5)
    finally:
        await feeder.stop()
```

## Cost Controls

### CPU
- **ScreenCaptureKit at 0.5 fps**: ~1-2% CPU
- **Synthetic mode**: <0.1% CPU
- **screencapture CLI at 0.5 fps**: ~5-8% CPU

### Memory
- **ScreenCaptureKit**: ~50-80MB (frame buffers)
- **Python synthetic**: ~20-30MB
- **screencapture CLI**: ~10-20MB (minimal, writes temp files)

### Disk
- **ScreenCaptureKit**: Zero disk I/O
- **Synthetic**: Zero disk I/O
- **screencapture CLI**: ~100KB/frame written then deleted

### Battery Impact at 0.5 fps
- **On AC power**: Negligible
- **On battery**: ~1-2% drain per hour (comparable to idle with Slack open)

## Emergency Cleanup

If you suspect a feeder is stuck running:

```bash
# Kill all screencapturekit feeders
pkill -f screencapturekit-feeder

# Kill Python feeders
pkill -f omni-visual-test-feeder

# Stop screen-capture-server if idle
lsof -ti :7900 | xargs kill
```

## Design Philosophy

The test feeders are designed as **ephemeral test fixtures**:
1. Start when test needs visual input
2. Run only during test
3. Stop when test completes
4. Leave no background processes

This is **not** a production screen recording daemon. For production continuous capture, use the existing `src/vision-tools.ts` pipeline which has scene detection, deduplication, and user-controlled lifecycle.

## CI/CD Considerations

When running in CI (GitHub Actions, etc.):
- Screen capture requires GUI session (won't work in headless CI)
- Use `mode='synthetic'` for CI tests
- ScreenCaptureKit will fail gracefully if Screen Recording permission is denied
- Set explicit timeouts on all visual tests

Example CI-safe test:
```python
@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Screen capture requires GUI session"
)
async def test_real_screen():
    feeder = ScreenCaptureKitFeeder()
    # ...

async def test_synthetic_always_works():
    feeder = OmniVisualTestFeeder(mode='synthetic')
    # Runs in CI and locally
```

## Summary

The visual test feeders are **safe by design**:
- No automatic startup
- No background persistence
- Process lifecycle tied to test
- Conservative default frame rate
- Explicit cleanup required

Follow the best practices above and the feeders will not hurt your Mac hardware or waste resources.
