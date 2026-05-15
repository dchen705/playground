"""
FastAPI dashboard backend — Postgres-only, no Phoenix required.

## Setup
  1. Make sure `.env` has:
     DBOS_SYSTEM_DATABASE_URL=postgresql://...
     OPENAI_API_KEY=...

  ## Run
  # Run the agent (pick any topic)
  uv run tests/research_agent.py "your topic here"

  # Start the backend
  uv run uvicorn dashboard_backend_2:app --reload

  ## Test
  # Open http://localhost:8000/docs
  # GET /workflows?limit=1   → grab the workflow_uuid
  # GET /workflows/{uuid}    → see the full JOIN with LLM + step data
"""
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from sdk import (
    init,
    list_workflows,
    get_workflow,
    get_steps,
    fetch_spans_for_workflow,
    build_step_records,
)

load_dotenv()

DB_URL = (
    os.environ.get("DB_URL")
    or os.environ.get("DBOS_SYSTEM_DATABASE_URL")
    or ""
)


# ── Pydantic models ───────────────────────────────────────────────────────────

class WorkflowSummary(BaseModel):
    workflow_uuid: str
    name: str
    status: str
    created_at: Optional[int]
    completed_at: Optional[int]
    recovery_attempts: Optional[int]


class StepRecord(BaseModel):
    step_id: int
    function_name: str
    status: str
    duration_ms: Optional[int]
    llm_model: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    tool_name: Optional[str]
    tool_args: Optional[str]


class WorkflowDetail(BaseModel):
    workflow: dict[str, Any]
    steps: list[StepRecord]


class RunAgentRequest(BaseModel):
    topic: str
    workflow_uuid: Optional[str] = None


class RunAgentResponse(BaseModel):
    workflow_uuid: str
    topic: str
    output: str


class ResumeWorkflowRequest(BaseModel):
    workflow_uuid: str


class ResumeWorkflowResponse(BaseModel):
    workflow_uuid: str
    status: str
    output: Optional[str]


# ── App startup ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from tests.research_agent import run_agent  # noqa: F401 — registers @workflow with DBOS
    init(
        name="research-assistant",
        db_url=DB_URL or None,
    )
    yield


app = FastAPI(title="Checkpoint Dashboard", version="0.2.0", lifespan=lifespan)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/workflows", response_model=list[WorkflowSummary])
def get_workflows(
    status: Optional[str] = Query(
        None, description="Filter by status (PENDING, SUCCESS, ERROR)"
    ),
    limit: int = Query(50, ge=1, le=1000, description="Maximum results to return"),
):
    """List workflows from DBOS, newest first."""
    rows = list_workflows(status=status, limit=limit)
    return [
        WorkflowSummary(
            workflow_uuid=r["workflow_uuid"],
            name=r["name"],
            status=r["status"],
            created_at=r["created_at"],
            completed_at=r["updated_at"],
            recovery_attempts=r["recovery_attempts"],
        )
        for r in rows
    ]


@app.get("/workflows/{workflow_id}", response_model=WorkflowDetail)
def get_workflow_detail(workflow_id: str):
    """Return full workflow info + per-step JOIN of DBOS steps + span data."""
    wf = get_workflow(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")

    steps = get_steps(workflow_id)
    all_spans = fetch_spans_for_workflow(workflow_id, DB_URL)
    step_records = build_step_records(steps, all_spans)

    return WorkflowDetail(workflow=wf, steps=step_records)


@app.post("/run-agent", response_model=RunAgentResponse)
async def trigger_run_agent(body: RunAgentRequest):
    """Start or reconnect to a research-assistant workflow.

    If workflow_uuid is provided, reconnects to that existing run.
    If omitted, starts a new workflow with a DBOS-generated UUID.
    """
    from dbos import DBOS
    from dbos._error import DBOSNonExistentWorkflowError
    from tests.research_agent import run_agent

    if body.workflow_uuid:
        try:
            handle = await DBOS.retrieve_workflow_async(body.workflow_uuid)
        except DBOSNonExistentWorkflowError:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow {body.workflow_uuid!r} not found in DBOS",
            )
    else:
        handle = await DBOS.start_workflow_async(run_agent, body.topic)

    output = await handle.get_result()
    return RunAgentResponse(
        workflow_uuid=handle.workflow_id,
        topic=body.topic,
        output=output,
    )


@app.post("/resume-workflow", response_model=ResumeWorkflowResponse)
async def resume_workflow(body: ResumeWorkflowRequest):
    """Reconnect to an existing PENDING workflow and wait for its result."""
    from dbos import DBOS
    from dbos._error import DBOSNonExistentWorkflowError

    wf = get_workflow(body.workflow_uuid)
    if wf is None:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow {body.workflow_uuid!r} not found",
        )
    if wf["status"] in ("SUCCESS", "ERROR"):
        raise HTTPException(
            status_code=400,
            detail=f"Workflow {body.workflow_uuid!r} already finished with status {wf['status']!r}",
        )

    try:
        handle = await DBOS.retrieve_workflow_async(body.workflow_uuid)
    except DBOSNonExistentWorkflowError:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow {body.workflow_uuid!r} not found in DBOS",
        )

    output = await handle.get_result()
    wf = get_workflow(body.workflow_uuid)
    return ResumeWorkflowResponse(
        workflow_uuid=body.workflow_uuid,
        status=wf["status"] if wf else "UNKNOWN",
        output=str(output) if output is not None else None,
    )
