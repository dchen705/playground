"""
Data-access utilities for the Checkpoint SDK.

Two layers:
  1. DBOS API wrappers — list/get workflows and steps
  2. Agent events — fetch from agent_events table and enrich step records
"""

from typing import Optional


# ── DBOS API wrappers ─────────────────────────────────────────────────────────

def _wf_to_dict(w) -> dict:
    return {
        "workflow_id":       w.workflow_id,
        "name":              w.name,
        "status":            w.status,
        "created_at":        w.created_at,
        "updated_at":        w.updated_at,
        "recovery_attempts": None,  # not exposed in WorkflowStatus
    }


async def list_workflows(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    """List workflows from DBOS, newest first."""
    from dbos import DBOS
    kwargs: dict = {"limit": limit, "sort_desc": True, "load_input": False, "load_output": False}
    if status:
        kwargs["status"] = status
    results = await DBOS.list_workflows_async(**kwargs)
    return [_wf_to_dict(w) for w in results]


async def get_workflow(workflow_uuid: str) -> Optional[dict]:
    """Return a single workflow by ID, or None if not found."""
    from dbos import DBOS
    results = await DBOS.list_workflows_async(
        workflow_ids=[workflow_uuid], load_input=False, load_output=False
    )
    return _wf_to_dict(results[0]) if results else None


async def get_steps(workflow_uuid: str) -> list[dict]:
    """Return all step records for a workflow."""
    from dbos import DBOS
    return await DBOS.list_workflow_steps_async(workflow_uuid)


# ── Agent events ──────────────────────────────────────────────────────────────

def fetch_agent_events(workflow_id: str, db_url: str) -> list[dict]:
    """Return all agent_events rows for a workflow, ordered by capture time."""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT span_id, step_id, event_type,
                       model, tokens_in, tokens_out, provider_response_id,
                       tool_name, tool_args, tool_result,
                       from_agent, to_agent, captured_at
                FROM agent_events
                WHERE workflow_id = %s
                ORDER BY captured_at
                """,
                (workflow_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── Step shaping ──────────────────────────────────────────────────────────────

def build_step_records(
    steps: list[dict],
    agent_events: list[dict] | None = None,
) -> list[dict]:
    """
    Shape DBOS step records for dashboard consumption.

    When agent_events are provided, enrich each step with LLM and tool data:
      - llm_response events are joined to steps by step_id
      - tool_call events are joined to steps by step_id
    Falls back to DBOS-only shape when agent_events is None or empty.
    """
    # Build lookup: step_id → first matching event of each relevant type.
    # tool_call events from sync tools carry step_id=null (the on_span_end fires
    # after the DBOS step context unwinds in the thread executor). For those we
    # fall back to positional matching by tool_name below.
    llm_by_step: dict[int, dict] = {}
    tool_by_step: dict[int, dict] = {}
    unmatched_tools: dict[str, list[dict]] = {}  # tool_name → ordered list
    if agent_events:
        for event in agent_events:
            sid = event.get("step_id")
            etype = event["event_type"]
            if etype == "llm_response":
                if sid is not None and sid not in llm_by_step:
                    llm_by_step[sid] = event
            elif etype == "tool_call":
                if sid is not None and sid not in tool_by_step:
                    tool_by_step[sid] = event
                elif sid is None:
                    unmatched_tools.setdefault(event["tool_name"], []).append(event)

    # Positional fallback: match unmatched tool_call events to tool steps
    # by function_name in DBOS step order. Handles same-tool parallel calls
    # correctly because both lists reflect actual execution order.
    for step in steps:
        step_id = step["function_id"]
        fn_name = step["function_name"]
        if step_id not in tool_by_step and fn_name in unmatched_tools and unmatched_tools[fn_name]:
            tool_by_step[step_id] = unmatched_tools[fn_name].pop(0)

    records = []
    for step in steps:
        step_id = step["function_id"]
        duration_ms = None
        if step.get("started_at_epoch_ms") and step.get("completed_at_epoch_ms"):
            duration_ms = step["completed_at_epoch_ms"] - step["started_at_epoch_ms"]

        llm = llm_by_step.get(step_id, {})
        tool = tool_by_step.get(step_id, {})

        records.append({
            "step_id":               step_id,
            "function_name":         step["function_name"],
            "status":                "SUCCESS" if step.get("error") is None else "ERROR",
            "duration_ms":           duration_ms,
            # LLM fields — populated for _model_call_step rows
            "llm_model":             llm.get("model"),
            "tokens_in":             llm.get("tokens_in"),
            "tokens_out":            llm.get("tokens_out"),
            "provider_response_id":  llm.get("provider_response_id"),
            # Tool fields — populated for tool step rows
            "tool_name":             tool.get("tool_name") or step["function_name"],
            "tool_args":             tool.get("tool_args"),
        })
    return records
