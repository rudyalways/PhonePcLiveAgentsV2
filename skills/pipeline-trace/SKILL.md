# Pipeline Trace

Real-time visualization of user operation pipeline checkpoints.

## Usage

```bash
python3 skills/pipeline-trace/scripts/pipeline-trace.py
# Open http://localhost:7848
```

## What it shows

- Every user operation (voice, Discord, Telegram, API) as a trace with 8 checkpoints
- Real-time status updates via SSE
- Service health overview
- Historical traces with timing

## Port

7848 (override with `PIPELINE_TRACE_PORT` env var)
