"""
(v2 - leaner version)
Dashboard backend — read-only, separate DBOS runtime, same Postgres DB as the executor.

## Run (run on port 8001 alongside agent with DBOS decorators)
  uv run uvicorn dashboard_backend_v2:app --port 8001

## Endpoints
  GET /health
  GET /workflows?status=PENDING&limit=50
  GET /workflows/{workflow_id}   ← DBOS-backed workflow + step history
"""
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sdk import (
    init,
    list_workflows,
    get_workflow,
    get_steps,
    build_step_records,
    fetch_agent_events_for_dashboard,
)

load_dotenv()

DB_URL = (
    os.environ.get("DB_URL")
    or os.environ.get("DBOS_SYSTEM_DATABASE_URL")
    or ""
)


# ── Pydantic models ───────────────────────────────────────────────────────────

class WorkflowSummary(BaseModel):
    workflow_id: str
    name: str
    status: str
    created_at: Optional[int]
    completed_at: Optional[int]
    recovery_attempts: Optional[int]


class StepRecord(BaseModel):
    step_id: Optional[int]
    function_name: Optional[str]
    status: str
    duration_ms: Optional[int]
    llm_model: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    provider_response_id: Optional[str]
    tool_name: Optional[str]
    tool_args: Any | None
    tool_match_status: Optional[str] = None


class AgentEvent(BaseModel):
    span_id: str
    step_id: Optional[int]
    event_type: str
    model: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    provider_response_id: Optional[str]
    tool_name: Optional[str]
    tool_args: Any | None
    tool_result: Optional[str]
    from_agent: Optional[str]
    to_agent: Optional[str]
    captured_at: Any


class WorkflowDetail(BaseModel):
    workflow: dict[str, Any]
    steps: list[StepRecord]
    events: list[AgentEvent]


# ── App startup ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # No workflow imports — this process is a pure reader.
    # DBOS will not attempt to execute or recover any workflows here.
    init(name="checkpoint-dashboard", db_url=DB_URL or None)
    yield


app = FastAPI(title="Checkpoint Dashboard", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/workflows", response_model=list[WorkflowSummary])
async def get_workflows(
    status: Optional[str] = Query(
        None, description="Filter by status (PENDING, SUCCESS, ERROR)"
    ),
    limit: int = Query(50, ge=1, le=1000, description="Maximum results to return"),
):
    """List workflows from DBOS, newest first."""
    rows = await list_workflows(status=status, limit=limit)
    return [
        WorkflowSummary(
            workflow_id=r["workflow_id"],
            name=r["name"],
            status=r["status"],
            created_at=r["created_at"],
            completed_at=r["updated_at"],
            recovery_attempts=r["recovery_attempts"],
        )
        for r in rows
    ]


@app.get("/workflows/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow_detail(workflow_id: str):
    """Return workflow info, enriched step history, and raw agent events."""
    wf = await get_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")

    steps = await get_steps(workflow_id)
    agent_events = await fetch_agent_events_for_dashboard(workflow_id, DB_URL) if DB_URL else []
    step_records = build_step_records(steps, agent_events)

    return WorkflowDetail(workflow=wf, steps=step_records, events=agent_events)
