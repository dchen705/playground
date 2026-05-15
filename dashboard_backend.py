"""
FastAPI dashboard backend — Postgres-only, no Phoenix required.

## Setup
  1. Make sure `.env` has:
     DBOS_SYSTEM_DATABASE_URL=postgresql://...
     OPENAI_API_KEY=...
     DBOS_CONDUCTOR_KEY=dbos...

  ## Run
  # Run the agent (pick any topic)
  uv run tests/research_agent.py "your topic here"

  # Start the backend
  uv run uvicorn dashboard_backend:app --reload

  ## Test
  # Open http://localhost:8000/docs
  # GET /workflows?limit=1   → grab the workflow_uuid
  # GET /workflows/{uuid}    → see the full JOIN with LLM + step data
"""
import json
import os
import textwrap
from contextlib import asynccontextmanager
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from tests.research_agent import run_agent  # registers the @workflow with DBOS

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


# ── DBOS data access (via Python API — no raw SQL against dbos.* tables) ─────

def list_workflows_dbos(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    from dbos import DBOS
    kwargs: dict = {"limit": limit, "sort_desc": True, "load_input": False, "load_output": False}
    if status:
        kwargs["status"] = status
    results = DBOS.list_workflows(**kwargs)
    return [_wf_to_dict(w) for w in results]


def get_workflow_dbos(workflow_uuid: str) -> Optional[dict]:
    from dbos import DBOS
    results = DBOS.list_workflows(
        workflow_ids=[workflow_uuid], load_input=False, load_output=False
    )
    return _wf_to_dict(results[0]) if results else None


def get_steps_dbos(workflow_uuid: str) -> list[dict]:
    from dbos import DBOS
    return DBOS.list_workflow_steps(workflow_uuid)


def _wf_to_dict(w) -> dict:
    return {
        "workflow_uuid":      w.workflow_id,
        "name":               w.name,
        "status":             w.status,
        "created_at":         w.created_at,
        "updated_at":         w.updated_at,
        "recovery_attempts":  None,  # not exposed in WorkflowStatus
    }


# ── Span data access (our public schema via OurSpanExporter) ─────────────────

def _spans_db() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    return conn


def fetch_spans_for_workflow(workflow_uuid: str) -> list[dict]:
    """Return all spans for the trace linked to this workflow_uuid."""
    conn = _spans_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT s.span_id, s.parent_span_id, s.span_kind,
               s.attributes, s.dbos_step_id, s.name
        FROM spans s
        JOIN traces t ON t.trace_id = s.trace_id
        WHERE t.workflow_id = %s
        ORDER BY s.start_time
        """,
        (workflow_uuid,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ── JOIN logic ────────────────────────────────────────────────────────────────

def build_step_records(steps: list[dict], all_spans: list[dict]) -> list[dict]:
    """
    JOIN DBOS step records with span data on dbos_step_id == function_id.

    Span tree shape (OpenInference / OpenAI Agents SDK):
        turn (CHAIN)
        ├── response (LLM)              ← model name + token counts
        └── search_web (TOOL, step)     ← step span IS the tool span; tool args live here
    """
    children: dict[Optional[str], list[dict]] = {}
    for s in all_spans:
        children.setdefault(s["parent_span_id"], []).append(s)

    step_spans_by_step_id: dict[int, dict] = {
        s["dbos_step_id"]: s
        for s in all_spans
        if s["dbos_step_id"] is not None
    }

    records = []
    for step in steps:
        step_span = step_spans_by_step_id.get(step["function_id"])

        # Tool args live on the step span itself (it IS the TOOL span)
        tool_attrs: dict = (step_span["attributes"] or {}) if step_span else {}

        # The step span's parent is the turn (CHAIN); response (LLM) is a sibling
        llm_attrs: dict = {}
        if step_span and step_span.get("parent_span_id"):
            turn_id = step_span["parent_span_id"]
            llm_span = next(
                (s for s in children.get(turn_id, []) if s["span_kind"] == "LLM"),
                None,
            )
            if llm_span:
                llm_attrs = llm_span["attributes"] or {}

        duration_ms = None
        if step.get("started_at_epoch_ms") and step.get("completed_at_epoch_ms"):
            duration_ms = step["completed_at_epoch_ms"] - step["started_at_epoch_ms"]

        raw_args = tool_attrs.get("input.value", "")
        try:
            short_args: Optional[str] = textwrap.shorten(
                json.dumps(json.loads(raw_args)), width=80
            )
        except (json.JSONDecodeError, TypeError):
            short_args = textwrap.shorten(str(raw_args), width=80) or None

        tok_in = llm_attrs.get("llm.token_count.prompt")
        tok_out = llm_attrs.get("llm.token_count.completion")

        records.append({
            "step_id":       step["function_id"],
            "function_name": step["function_name"],
            "status":        "SUCCESS" if step.get("error") is None else "ERROR",
            "duration_ms":   duration_ms,
            "llm_model":     llm_attrs.get("llm.model_name"),
            "tokens_in":     int(tok_in) if tok_in is not None else None,
            "tokens_out":    int(tok_out) if tok_out is not None else None,
            "tool_name":     tool_attrs.get("tool.name") or step["function_name"],
            "tool_args":     short_args,
        })
    return records


# ── App startup ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from sdk import init
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
    rows = list_workflows_dbos(status=status, limit=limit)
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
    wf = get_workflow_dbos(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found")

    steps = get_steps_dbos(workflow_id)
    all_spans = fetch_spans_for_workflow(workflow_id)
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

    wf = get_workflow_dbos(body.workflow_uuid)
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
    wf = get_workflow_dbos(body.workflow_uuid)
    return ResumeWorkflowResponse(
        workflow_uuid=body.workflow_uuid,
        status=wf["status"] if wf else "UNKNOWN",
        output=str(output) if output is not None else None,
    )
