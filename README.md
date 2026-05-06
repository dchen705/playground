Durable Execution Playground

A/B testing ground for evaluating durable execution frameworks (Inngest, DBOS, ours) across the same agent workflows.

## Quick Start

```bash
export OPENAI_API_KEY=sk-...

# install uv
curl -LsSf https://astral.sh | sh

# try inngest
cd inngest
# 1. Start the Inngest dev server
npx --ignore-scripts=false inngest-cli@latest dev -u http://127.0.0.1:8000/api/inngest --no-discovery

# 2. Start the agent server (in a separate terminal)
INNGEST_DEV=1 uv run uvicorn server:app --reload

# Send an event via your app's Inngest endpoint
curl -X POST http://127.0.0.1:8000/api/inngest \
  -H "Content-Type: application/json" \
  -d '{
    "name": "agent/run",
    "data": {}
  }'
# open http://localhost:8288 to see the inngest dashboard
```

