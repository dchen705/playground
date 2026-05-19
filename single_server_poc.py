"""
Single-server proof-of-concept: executor + reader in one DBOS process.

Tests whether GET /workflows can introspect concurrently while a workflow executes.
The run_agent workflow starts with a 30-second DBOS sleep — use that window to hit
GET /workflows or GET /workflows/{id} in Swagger and confirm there's no blocking.

## Run
  uv run uvicorn single_server_poc:app --port 8002

## Endpoints
  POST /run?topic=<topic>          → start workflow, returns workflow_id
  GET  /workflows                  → list all workflows (test concurrent read)
  GET  /workflows/{workflow_id}    → workflow detail + steps
  GET  /health
"""
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from agents import Agent, function_tool
from ddgs import DDGS
from dbos import DBOS
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from sdk import agentic_runner, get_steps, get_workflow, init, list_workflows, sleep, step, workflow

load_dotenv()

DB_URL = (
    os.environ.get("DB_URL")
    or os.environ.get("DBOS_SYSTEM_DATABASE_URL")
    or ""
)


# ── Agent ─────────────────────────────────────────────────────────────────────

@function_tool
@step()
def search_web(query: str) -> str:
    """Search the web for information about a topic. Returns titles, URLs, and summaries."""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    if not results:
        return "No results found."
    formatted = []
    for r in results:
        formatted.append(f"Title: {r['title']}\nURL: {r['href']}\nSummary: {r['body']}")
    return "\n---\n".join(formatted)


agent = Agent(
    name="research-assistant",
    instructions="""You are a research assistant. Given a topic:
1. Search for information using search_web
2. Evaluate whether you have enough to write a thorough summary
3. If not, search again with a more specific or different query
4. Search at least twice before concluding
5. Synthesize findings into a clear, well-structured summary
Be explicit about what you found and what remains uncertain.""",
    tools=[search_web],
)


@workflow()
async def run_agent(topic: str) -> str:
    await sleep(30)
    result = await agentic_runner(
        starting_agent=agent,
        input=f"Research this topic thoroughly: {topic}",
    )
    return str(result.final_output)


# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(DB_URL)
    init(name="single-server-poc", db_url=DB_URL or None)
    yield


app = FastAPI(
    title="Single-Server POC",
    description="Validates that DBOS introspection routes work concurrently with workflow execution in one process.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run")
async def run(
    topic: str = Query(description="Research topic to investigate"),
) -> dict[str, str]:
    """Start a research workflow. The workflow sleeps 30s first — use that window to test GET /workflows."""
    handle = await DBOS.start_workflow_async(run_agent, topic)
    return {"workflow_id": handle.workflow_id}


@app.get("/workflows")
async def get_workflows(
    status: Optional[str] = Query(None, description="Filter: PENDING, SUCCESS, ERROR"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """List workflows. Call this while POST /run workflow is sleeping to confirm no blocking."""
    return await list_workflows(status=status, limit=limit)


@app.get("/workflows/{workflow_id}")
async def get_workflow_detail(workflow_id: str) -> dict[str, Any]:
    """Return workflow info and step list."""
    wf = await get_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")
    steps = await get_steps(workflow_id)
    return {"workflow": wf, "steps": steps}
