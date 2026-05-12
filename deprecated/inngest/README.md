## Setup

```bash
# 1. Start the Inngest dev server
npx --ignore-scripts=false inngest-cli@latest dev -u http://127.0.0.1:8000/api/inngest --no-discovery

# 2. Start the agent server (in a separate terminal)
INNGEST_DEV=1 uv run uvicorn server:app --reload
```

## Trigger a Run

```bash
# Send an event via your app's Inngest endpoint
curl -X POST http://127.0.0.1:8000/api/inngest \
  -H "Content-Type: application/json" \
  -d '{
    "name": "agent/run",
    "data": {}
  }'
```

## Dashboard

Open **http://localhost:8288** to see:

## Architecture

```
┌─────────────┐     events      ┌──────────────────┐
│ Inngest Dev  │ ──────────────► │  Your App        │
│ Server       │ ◄────────────── │  (serve.py)      │
│ :8288        │   step results  │  :8000           │
│              │                 │                  │
│  Dashboard   │                 │  agent.py        │
│  Event queue │                 │  ├─ step.run()   │
│  Retry logic │                 │  ├─ step.run()   │
│              │                 │  └─ ...          │
└─────────────┘                 └──────────────────┘
```

The Inngest dev server is the orchestrator. It sends events to your app,
your app executes steps and reports back. The dev server handles retries,
checkpointing, and the dashboard.
