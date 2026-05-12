# Durable Execution Playground

A development space for building and testing our SDK — a durable execution wrapper over DBOS for agents and agentic workflows.

## Structure

```
playground/
├── sdk/          # the SDK library (edit this to develop)
│   └── src/sdk/
│       ├── __init__.py
│       └── decorators.py   # workflow, step, sleep, init
├── tests/        # test scripts that use the SDK
└── deprecated/   # prior reference implementations (Inngest, raw DBOS)
```

## Prerequisites

**1. Install uv**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2a. Create env file**
```bash
cp env.example .env

```

**2b. Set your OpenAI key**
```bash
OPENAI_API_KEY=sk-...
```

**3. Install dependencies**
```bash
cd playground

// activate virtual env
source .venv/bin/activate

uv sync

// To deactivate virtual env
deactivate
```



This installs all dependencies including the `sdk` package in editable mode — changes to `sdk/` are reflected immediately without reinstalling.

## Running Tests

All test scripts are in `tests/`. Run them with `uv run` from the `playground/` root:

```bash
# Two steps and a durable sleep
uv run python tests/counter.py

# Test a workflow
uv run python tests/event_booking.py
```

> By default, tests use SQLite as the system database (no setup needed). A file named `[app-name].sqlite` is created in the directory you run from.

## Developing the SDK

The SDK lives in `sdk/src/sdk/`. Since it's installed as an editable package, you can edit it and re-run tests without any reinstall step.

**Current public API:**

```python
from sdk import init, workflow, step, sleep
```

| Function | What it does |
|---|---|
| `init(name, db_url, traces_endpoint, env)` | Configure and launch DBOS — call once in `__main__` |
| `@workflow()` | Mark a function as a durable workflow |
| `@step()` | Mark a function as a checkpointed step |
| `sleep(seconds)` | Durable sleep — skips elapsed time on crash recovery |

**Writing a new test:**

```python
from sdk import workflow, step, init

@step()
def call_external_api():
    # anything with side effects goes in a step
    ...

@workflow()
def my_workflow():
    result = call_external_api()
    return result

if __name__ == "__main__":
    init(name="my-test")
    my_workflow()
```


## Using Postgres (optional)

By default the SDK uses SQLite, which is fine for local development. To use Postgres:

```bash
export CHECKPOINT_DB_URL=postgresql://user:password@localhost:5432/mydb
uv run python tests/event_booking.py
```

Or pass it directly:

```python
init(name="my-app", db_url="postgresql://...")
```

## Adding Tracing

Pass an OTLP endpoint to `init()` to enable OpenTelemetry traces for every workflow and step:

```python
init(
    name="my-app",
    traces_endpoint="https://your-collector:4318/v1/traces",
    env="production",
)
```

OTel-compatible backends: Datadog, Grafana Tempo, Honeycomb, Jaeger, etc.
