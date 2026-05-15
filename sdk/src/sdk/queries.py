"""
Data-access utilities for the Checkpoint SDK.

Provides two layers:
  1. DBOS API wrappers — list/get workflows and steps via the DBOS Python API
  2. Span / JOIN logic — fetch OTel spans from Postgres and correlate them with
     DBOS step records to produce enriched StepRecord dicts
"""

import json
import textwrap
from typing import Optional

import psycopg2
import psycopg2.extras


# ── DBOS API wrappers ─────────────────────────────────────────────────────────

def _wf_to_dict(w) -> dict:
    return {
        "workflow_uuid":     w.workflow_id,
        "name":              w.name,
        "status":            w.status,
        "created_at":        w.created_at,
        "updated_at":        w.updated_at,
        "recovery_attempts": None,  # not exposed in WorkflowStatus
    }


def list_workflows(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    """List workflows from DBOS, newest first."""
    from dbos import DBOS
    kwargs: dict = {"limit": limit, "sort_desc": True, "load_input": False, "load_output": False}
    if status:
        kwargs["status"] = status
    results = DBOS.list_workflows(**kwargs)
    return [_wf_to_dict(w) for w in results]


def get_workflow(workflow_uuid: str) -> Optional[dict]:
    """Return a single workflow by ID, or None if not found."""
    from dbos import DBOS
    results = DBOS.list_workflows(
        workflow_ids=[workflow_uuid], load_input=False, load_output=False
    )
    return _wf_to_dict(results[0]) if results else None


def get_steps(workflow_uuid: str) -> list[dict]:
    """Return all step records for a workflow."""
    from dbos import DBOS
    return DBOS.list_workflow_steps(workflow_uuid)


# ── Span data access ──────────────────────────────────────────────────────────

def fetch_spans_for_workflow(workflow_uuid: str, db_url: str) -> list[dict]:
    """Return all spans for the trace linked to this workflow_uuid."""
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
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
