# DBOS — WIP - NOT USABLE YET

Runs the agent loop with each LLM call and tool execution as a separate
**durable step** via DBOS's `@DBOS.step()` decorator.

## What DBOS Gives You

- **Decorator-based durability** — `@DBOS.step()` and `@DBOS.workflow()`, no SDK-specific wrapping logic
- **PostgreSQL-backed** — all step results stored in Postgres, no separate orchestrator process
- **Cloud dashboard** at `https://console.dbos.dev` for run visibility
- **Automatic recovery** — if the process crashes, restart it and workflows resume from the last completed step

## Setup

```bash
# 1. Install deps
pip install -e .

# 2. Symlink shared tools and workflows (one-time)
ln -sf ../tools tools
ln -sf ../workflows workflows

# 3. Start PostgreSQL (if not already running)
#    DBOS needs a Postgres database for durability.
#    Easiest: use Docker
docker run -d --name playground-postgres \
  -e POSTGRES_PASSWORD=playground \
  -e POSTGRES_DB=playground \
  -p 5432:5432 \
  postgres:16

# 4. Configure DBOS (creates dbos-config.yaml if needed)
#    Set your database connection string:
export DBOS_DATABASE_URL=postgresql://postgres:playground@localhost:5432/playground

# 5. Start the server
export OPENAI_API_KEY=sk-...
python serve.py
```

## Trigger a Run

```bash
curl -X POST http://localhost:8001/run \
  -H "Content-Type: application/json" \
  -d '{
    "workflow": "deep_research",
    "input": "What are the latest developments in quantum computing?"
  }'
```

## Dashboard

Sign up at **https://console.dbos.dev** and connect your app to see:
- Workflow run list with status
- Step-by-step execution trace
- Step inputs and outputs
- Timing and retry info

## Things to Evaluate

- [ ] How does the decorator-based API compare to Inngest's step.run() wrapping?
- [ ] How readable is the step trace in the dashboard?
- [ ] What does recovery look like after a crash?
- [ ] How much overhead does the PostgreSQL checkpointing add?
- [ ] How does the cloud-only dashboard compare to Inngest's local dev server UI?
- [ ] Can you query the PostgreSQL tables directly for custom analysis?

## Architecture

```
┌──────────────────┐         ┌─────────────┐
│  Your App        │ ──────► │ PostgreSQL   │
│  (serve.py)      │ ◄────── │ :5432        │
│  :8001           │         │              │
│                  │         │ Step results │
│  @DBOS.workflow  │         │ Workflow log │
│  @DBOS.step      │         │ Recovery data│
└──────────────────┘         └─────────────┘
        │
        ▼
┌──────────────────┐
│ DBOS Cloud       │
│ console.dbos.dev │
│                  │
│ Dashboard        │
│ Monitoring       │
└──────────────────┘
```

No separate orchestrator — DBOS runs inside your process and uses
PostgreSQL for durability. The cloud dashboard is optional but
recommended for visibility.
