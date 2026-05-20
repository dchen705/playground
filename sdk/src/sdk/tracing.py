"""
Crash-safe agent observability via the OpenAI Agents SDK native TracingProcessor.

Writes LLM, tool, and handoff events synchronously to Postgres inside the
executing DBOS step — before the step returns and before DBOS checkpoints it.
This makes llm_response and tool_call events durable by construction.

Durability by event type:
  llm_response  — fires inside _model_call_step            → durable
  tool_call     — fires inside the tool's DBOS step        → durable
  handoff       — fires in runner loop between steps        → best-effort

Observability failures are swallowed: a transient DB error logs and continues
rather than crashing the agent. The agent running correctly takes priority.
"""

import json
import logging
import threading
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.span_data import (
    FunctionSpanData,
    GenerationSpanData,
    HandoffSpanData,
    ResponseSpanData,
)
from dbos import DBOS

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 3
_STATEMENT_TIMEOUT_MS = 3000
_LOCK_TIMEOUT_MS = 1000
_POOL_MIN_CONN = 1
_POOL_MAX_CONN = 4
_POOL_LOCK = threading.Lock()
_POOLS: dict[str, psycopg2.pool.ThreadedConnectionPool] = {}
_REGISTERED_PROCESSORS: set[str] = set()
_REGISTERED_PROCESSORS_LOCK = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS agent_events (
    id                   BIGSERIAL    PRIMARY KEY,
    event_key            TEXT         NOT NULL,
    span_id              TEXT         NOT NULL,
    workflow_id          TEXT         NOT NULL,
    step_id              INTEGER      NULL,
    event_type           TEXT         NOT NULL,
    model                TEXT         NULL,
    tokens_in            INTEGER      NULL,
    tokens_out           INTEGER      NULL,
    provider_response_id TEXT         NULL,
    tool_name            TEXT         NULL,
    tool_args            JSONB        NULL,
    tool_result          TEXT         NULL,
    from_agent           TEXT         NULL,
    to_agent             TEXT         NULL,
    captured_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_events_workflow_id ON agent_events (workflow_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_captured_at ON agent_events (captured_at DESC);
"""

_MIGRATION_SQL = """
ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS event_key TEXT;
UPDATE agent_events
SET event_key = CONCAT_WS(
    '|',
    COALESCE(workflow_id, ''),
    COALESCE(step_id::TEXT, ''),
    COALESCE(span_id, ''),
    COALESCE(event_type, ''),
    COALESCE(provider_response_id, ''),
    COALESCE(tool_name, '')
)
WHERE event_key IS NULL;
ALTER TABLE agent_events ALTER COLUMN event_key SET NOT NULL;
ALTER TABLE agent_events DROP CONSTRAINT IF EXISTS agent_events_span_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_event_key ON agent_events (event_key);
"""


def _connect_kwargs() -> dict[str, Any]:
    return {
        "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
        "options": (
            f"-c statement_timeout={_STATEMENT_TIMEOUT_MS} "
            f"-c lock_timeout={_LOCK_TIMEOUT_MS}"
        ),
    }


def _get_pool(db_url: str) -> psycopg2.pool.ThreadedConnectionPool:
    with _POOL_LOCK:
        pool = _POOLS.get(db_url)
        if pool is None:
            pool = psycopg2.pool.ThreadedConnectionPool(
                _POOL_MIN_CONN,
                _POOL_MAX_CONN,
                db_url,
                **_connect_kwargs(),
            )
            _POOLS[db_url] = pool
        return pool


def ensure_tables(db_url: str) -> None:
    """Create agent_events table if it does not exist. Called once at init."""
    conn = psycopg2.connect(db_url, **_connect_kwargs())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(_MIGRATION_SQL)
        conn.commit()
    finally:
        conn.close()


def register_checkpoint_tracing_processor(db_url: str) -> None:
    """Register one CheckpointTracingProcessor per DB URL for this process."""
    with _REGISTERED_PROCESSORS_LOCK:
        if db_url in _REGISTERED_PROCESSORS:
            return

        from agents.tracing import add_trace_processor

        ensure_tables(db_url)
        add_trace_processor(CheckpointTracingProcessor(db_url))
        _REGISTERED_PROCESSORS.add(db_url)


def _write_agent_event(db_url: str, record: dict[str, Any]) -> None:
    """Single synchronous INSERT; exact duplicate span attempts are ignored."""
    pool = _get_pool(db_url)
    conn = pool.getconn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO agent_events (
                    event_key, span_id, workflow_id, step_id, event_type,
                    model, tokens_in, tokens_out, provider_response_id,
                    tool_name, tool_args, tool_result,
                    from_agent, to_agent
                ) VALUES %s
                ON CONFLICT (event_key) DO NOTHING
                """,
                [(
                    record["event_key"],
                    record["span_id"],
                    record["workflow_id"],
                    record.get("step_id"),
                    record["event_type"],
                    record.get("model"),
                    record.get("tokens_in"),
                    record.get("tokens_out"),
                    record.get("provider_response_id"),
                    record.get("tool_name"),
                    psycopg2.extras.Json(record["tool_args"]) if record.get("tool_args") is not None else None,
                    record.get("tool_result"),
                    record.get("from_agent"),
                    record.get("to_agent"),
                )],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


class CheckpointTracingProcessor(TracingProcessor):
    """
    OpenAI Agents SDK TracingProcessor that writes agent events to Postgres
    synchronously in on_span_end.

    Successful llm_response and in-step tool_call writes complete before DBOS
    checkpoints the enclosing step. Failed writes are logged and swallowed, and
    handoff events remain best-effort because they fire between steps.
    """

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._span_step_ids: dict[str, int | None] = {}
        self._span_step_ids_lock = threading.Lock()

    def on_trace_start(self, trace: Any) -> None:
        pass

    def on_trace_end(self, trace: Any) -> None:
        pass

    def on_span_start(self, span: Any) -> None:
        span_id = getattr(span, "span_id", None)
        if not span_id:
            return
        with self._span_step_ids_lock:
            self._span_step_ids[span_id] = _current_step_id()

    def on_span_end(self, span: Any) -> None:
        try:
            workflow_id = DBOS.workflow_id
            if not workflow_id:
                return  # outside a DBOS workflow context

            data = span.span_data
            record: dict[str, Any] | None = None

            if isinstance(data, GenerationSpanData):
                usage = data.usage or {}
                record = {
                    "event_type": "llm_response",
                    "model":      data.model,
                    "tokens_in":  usage.get("input_tokens"),
                    "tokens_out": usage.get("output_tokens"),
                }

            elif isinstance(data, ResponseSpanData):
                usage = data.usage or {}
                record = {
                    "event_type":           "llm_response",
                    "model":                getattr(data.response, "model", None) if data.response else None,
                    "tokens_in":            usage.get("input_tokens"),
                    "tokens_out":           usage.get("output_tokens"),
                    "provider_response_id": data.response.id if data.response else None,
                }

            elif isinstance(data, FunctionSpanData):
                tool_args = _parse_json_or_str(data.input)
                record = {
                    "event_type":  "tool_call",
                    "tool_name":   data.name,
                    "tool_args":   tool_args,
                    "tool_result": None if data.output is None else str(data.output)[:500],
                }

            elif isinstance(data, HandoffSpanData):
                record = {
                    "event_type": "handoff",
                    "from_agent": data.from_agent,
                    "to_agent":   data.to_agent,
                }

            span_id = span.span_id
            if record is None:
                self._pop_started_step_id(span_id)
                return

            step_id = self._pop_started_step_id(span_id)
            if step_id is None:
                step_id = _current_step_id()

            record["span_id"]     = span_id
            record["workflow_id"] = workflow_id
            record["step_id"]     = step_id
            record["event_key"]   = _event_key(record, span)
            _write_agent_event(self._db_url, record)

        except Exception:
            logger.exception("CheckpointTracingProcessor: failed to write event, continuing")

    def force_flush(self) -> None:
        pass  # writes are synchronous — nothing to flush

    def shutdown(self) -> None:
        with _POOL_LOCK:
            pool = _POOLS.pop(self._db_url, None)
        if pool is not None:
            pool.closeall()

    def _pop_started_step_id(self, span_id: str) -> int | None:
        with self._span_step_ids_lock:
            return self._span_step_ids.pop(span_id, None)


def _event_key(record: dict[str, Any], span: Any) -> str:
    """
    Deduplicate exact duplicate callbacks while allowing DBOS retries to persist
    separate attempts when they run in a different step context.
    """
    parts = [
        record.get("workflow_id"),
        record.get("step_id"),
        record.get("span_id"),
        record.get("event_type"),
        record.get("provider_response_id"),
        record.get("tool_name"),
    ]
    return "|".join("" if part is None else str(part) for part in parts)


def _current_step_id() -> int | None:
    try:
        return DBOS.step_id
    except Exception:
        return None


def _parse_json_or_str(value: str | None) -> Any:
    """Try to parse a JSON string into a dict/list; return raw string on failure."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
