import asyncio
import functools
import os
from dbos import DBOS, DBOSConfig
from typing import Optional, Callable, Union, Awaitable

def workflow(
    *,
    name: str | None = None,
    max_recovery_attempts: int | None = 10
):
    dbos_workflow = DBOS.workflow(name=name, max_recovery_attempts=max_recovery_attempts)

    def decorator(fn):
        @functools.wraps(fn)
        async def with_root_span(*args, **kwargs):
            from opentelemetry import trace
            tracer = trace.get_tracer("sdk")
            # DBOS has set up its context by the time it calls this function,
            # so DBOS.workflow_id is available here. We create the trace root span
            # and stamp the workflow_id so the exporter can populate traces.workflow_id.
            with tracer.start_as_current_span(fn.__name__) as span:
                wf_id = DBOS.workflow_id
                if wf_id and span.is_recording():
                    span.set_attribute("dbos.workflow_id", wf_id)
                return await fn(*args, **kwargs)

        return dbos_workflow(with_root_span)

    return decorator

def step(
    *,
    name: Optional[str] = None,
    retries_allowed: bool = False,
    interval_seconds: float = 1.0,
    max_attempts: int = 3,
    backoff_rate: float = 2.0,
    should_retry: Optional[
        Callable[[BaseException], Union[bool, Awaitable[bool]]]
    ] = None,
):
    dbos_step = DBOS.step(
        name=name,
        retries_allowed=retries_allowed,
        interval_seconds=interval_seconds,
        max_attempts=max_attempts,
        backoff_rate=backoff_rate,
        should_retry=should_retry,
    )

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def stamped(*args, **kwargs):
                span = DBOS.span
                step_id = DBOS.step_id
                if span is not None and step_id is not None:
                    span.set_attribute("dbos.step_id", step_id)
                return await fn(*args, **kwargs)
        else:
            @functools.wraps(fn)
            def stamped(*args, **kwargs):
                span = DBOS.span
                step_id = DBOS.step_id
                if span is not None and step_id is not None:
                    span.set_attribute("dbos.step_id", step_id)
                return fn(*args, **kwargs)
        return dbos_step(stamped)

    return decorator

def sleep(*args, **kwargs):
    return DBOS.sleep(*args, **kwargs)

def init(
    name: str,
    db_url: str | None = None,
    conductor_key: str | None = None,
    traces_endpoint: str | None = None,
    env: str | None = None,
) -> None:
    resolved_env = env or os.environ.get("CHECKPOINT_ENV", "development")
    resolved_db = (
        db_url
        or os.environ.get("DB_URL")
        or os.environ.get("DBOS_SYSTEM_DATABASE_URL")
        or os.environ.get("CHECKPOINT_DB_URL")
    )
    resolved_conductor_key = conductor_key or os.environ.get("CHECKPOINT_CONDUCTOR_KEY")

    config: DBOSConfig = {
        "name": name,
        # Falls back to SQLite ([name].sqlite) if not set — good for local dev
        "system_database_url": resolved_db,
        # We will keep this conductor key for now so we can utilize their dashboard
        # We can remove when we solidify our SDK
        "conductor_key": resolved_conductor_key,
        # OTLP tracing: only enabled when an endpoint is provided
        "enable_otlp": traces_endpoint is not None,
        "otlp_traces_endpoints": [traces_endpoint] if traces_endpoint else None,
        "otlp_attributes": {"env": resolved_env, "sdk": "checkpoint"},
        "otel_attribute_format": "semconv",
        # Safe default for connection poolers (Supabase, PgBouncer, Neon)
        "use_listen_notify": resolved_db is not None
        and resolved_db.startswith("postgresql"),
    }

    DBOS(config=config)
    DBOS.launch()

    if resolved_db and resolved_db.startswith("postgresql"):
        _add_our_span_exporter(resolved_db)

    _instrument_openai_agents()


def _add_our_span_exporter(db_url: str) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from sdk.exporter import OurSpanExporter

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)

    provider.add_span_processor(BatchSpanProcessor(OurSpanExporter(db_url=db_url)))


def _instrument_openai_agents() -> None:
    from opentelemetry import trace
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    OpenAIAgentsInstrumentor().instrument(tracer_provider=trace.get_tracer_provider())
