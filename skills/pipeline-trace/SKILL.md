# Pipeline Trace

Real-time visualization of user operation pipeline checkpoints.

## Usage

```bash
python3 skills/pipeline-trace/scripts/pipeline-trace.py
# Open http://localhost:7902
```

## What it shows

- Every user operation (voice, Discord, Telegram, API) as a trace with checkpoints (执行方式 + ①–⑥ core 链路 when applicable + 回复)
- Real-time status updates via SSE
- Service health overview
- Historical traces with timing

## Port

7902 (override with `PIPELINE_TRACE_PORT` env var)
