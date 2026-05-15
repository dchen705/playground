import json
import logging
from datetime import datetime, timezone
from typing import Optional, Sequence

import psycopg2
import psycopg2.extras
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)

_NS_PER_SEC = 1_000_000_000

_DDL = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id    TEXT        PRIMARY KEY,
    start_time  TIMESTAMPTZ NOT NULL,
    end_time    TIMESTAMPTZ NOT NULL,
    workflow_id TEXT        NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_start_time  ON traces (start_time DESC);
CREATE INDEX IF NOT EXISTS idx_traces_workflow_id ON traces (workflow_id) WHERE workflow_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS spans (
    trace_id                    TEXT        NOT NULL REFERENCES traces(trace_id),
    span_id                     TEXT        PRIMARY KEY,
    parent_span_id              TEXT        NULL,
    name                        TEXT        NOT NULL,
    span_kind                   TEXT        NOT NULL,
    start_time                  TIMESTAMPTZ NOT NULL,
    end_time                    TIMESTAMPTZ NULL,
    attributes                  JSONB       NOT NULL DEFAULT '{}',
    dbos_step_id                INTEGER     NULL,
    status_code                 TEXT        NOT NULL DEFAULT 'UNSET',
    status_message              TEXT        NULL,
    llm_token_count_prompt      INTEGER     NULL,
    llm_token_count_completion  INTEGER     NULL,
    events                      JSONB       NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_spans_trace_id     ON spans (trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_dbos_step_id ON spans (dbos_step_id) WHERE dbos_step_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_spans_start_time   ON spans (start_time DESC);
"""


class OurSpanExporter(SpanExporter):
    """
    Synchronous OTel SpanExporter that writes all spans directly to Postgres.

    Captures the full span tree for each agent run (LLM, TOOL, CHAIN, INTERNAL/DBOS
    spans) into two tables:
      - traces: one row per trace_id, for fast listing of agent runs
      - spans:  one row per span, with dbos_step_id extracted for JOIN against
                dbos.operation_outputs.function_id

    Designed for use with BatchSpanProcessor, which calls export() from a single
    background daemon thread — synchronous psycopg2 is correct here.
    """

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._conn: Optional[psycopg2.extensions.connection] = None
        self._ensure_tables()

    # ── Connection ────────────────────────────────────────────────────────────

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._db_url)
            self._conn.autocommit = False
        return self._conn

    # ── DDL bootstrap ─────────────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()

    # ── Export ────────────────────────────────────────────────────────────────

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            trace_rows: dict[str, tuple] = {}
            span_rows: list[tuple] = []

            for span in spans:
                t_row, s_row = self._to_rows(span)
                trace_id = t_row[0]
                # Merge time bounds across spans in the same batch sharing a trace_id
                if trace_id in trace_rows:
                    existing = trace_rows[trace_id]
                    # (trace_id, start_time, end_time, workflow_id)
                    merged_start = min(existing[1], t_row[1])
                    merged_end = max(existing[2], t_row[2])
                    merged_wf = existing[3] or t_row[3]
                    trace_rows[trace_id] = (trace_id, merged_start, merged_end, merged_wf)
                else:
                    trace_rows[trace_id] = t_row
                span_rows.append(s_row)

            conn = self._get_conn()
            with conn.cursor() as cur:
                # Upsert traces first (FK constraint requires this)
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO traces (trace_id, start_time, end_time, workflow_id)
                    VALUES %s
                    ON CONFLICT (trace_id) DO UPDATE SET
                        start_time  = LEAST(traces.start_time, EXCLUDED.start_time),
                        end_time    = GREATEST(traces.end_time, EXCLUDED.end_time),
                        workflow_id = COALESCE(traces.workflow_id, EXCLUDED.workflow_id)
                    """,
                    list(trace_rows.values()),
                )
                # Insert spans — silently ignore duplicates
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO spans (
                        trace_id, span_id, parent_span_id,
                        name, span_kind,
                        start_time, end_time,
                        attributes,
                        dbos_step_id,
                        status_code, status_message,
                        llm_token_count_prompt, llm_token_count_completion,
                        events
                    ) VALUES %s
                    ON CONFLICT (span_id) DO NOTHING
                    """,
                    span_rows,
                )
            conn.commit()
            return SpanExportResult.SUCCESS

        except Exception:
            logger.exception("OurSpanExporter: failed to export %d spans", len(spans))
            try:
                if self._conn:
                    self._conn.rollback()
                    self._conn = None  # force reconnect on next call
            except Exception:
                pass
            return SpanExportResult.FAILURE

    # ── Conversion ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_rows(span: ReadableSpan) -> tuple[tuple, tuple]:
        ctx = span.context
        trace_id = format(ctx.trace_id, "032x")
        span_id  = format(ctx.span_id,  "016x")

        parent_span_id: Optional[str] = None
        if span.parent is not None:
            parent_span_id = format(span.parent.span_id, "016x")

        start_time = _ns_to_dt(span.start_time)
        end_time   = _ns_to_dt(span.end_time) if span.end_time else start_time

        attrs: dict = dict(span.attributes or {})

        dbos_step_id   = _safe_int(attrs.get("dbos.step_id"))
        prompt_tokens  = _safe_int(attrs.get("llm.token_count.prompt"))
        compl_tokens   = _safe_int(attrs.get("llm.token_count.completion"))
        workflow_id    = attrs.get("dbos.workflow_id") or attrs.get("operationUUID") or None

        status_code    = span.status.status_code.name  # UNSET, OK, ERROR
        status_message = span.status.description or None

        # OpenInference stores the semantic kind (LLM, TOOL, CHAIN, AGENT…) as an
        # attribute — OTel's SpanKind is always INTERNAL for these spans.
        span_kind = attrs.get("openinference.span.kind") or span.kind.name

        events = [
            {
                "name":       e.name,
                "timestamp":  _ns_to_dt(e.timestamp).isoformat() if e.timestamp else None,
                "attributes": dict(e.attributes or {}),
            }
            for e in (span.events or [])
        ]

        trace_row = (trace_id, start_time, end_time, workflow_id)
        span_row  = (
            trace_id,
            span_id,
            parent_span_id,
            span.name,
            span_kind,
            start_time,
            end_time,
            psycopg2.extras.Json(attrs),
            dbos_step_id,
            status_code,
            status_message,
            prompt_tokens,
            compl_tokens,
            psycopg2.extras.Json(events),
        )
        return trace_row, span_row

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        # BatchSpanProcessor already flushed before calling this;
        # psycopg2 writes are synchronous so nothing to do.
        return True


def _ns_to_dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / _NS_PER_SEC, tz=timezone.utc)


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
