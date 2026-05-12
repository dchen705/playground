"""
DBOS entrypoint.

DBOS can serve HTTP endpoints directly via FastAPI.
Unlike Inngest, there's no separate dev server — DBOS uses PostgreSQL
for durability and its cloud dashboard for observability.
"""

import uvicorn
from fastapi import FastAPI
from dbos import DBOS
from agent import run_agent

app = FastAPI()
DBOS(fastapi=app)


@app.get("/")
def health():
    return {"status": "ok", "framework": "dbos"}


@app.post("/run")
def trigger_run(payload: dict):
    """Trigger an agent run."""
    workflow_name = payload.get("workflow", "deep_research")
    user_message = payload["input"]

    # DBOS.workflow runs are automatically tracked and durable
    result = run_agent(workflow_name, user_message)
    return result


if __name__ == "__main__":
    DBOS.launch()
    uvicorn.run(app, host="0.0.0.0", port=8001)
