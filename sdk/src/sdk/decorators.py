import os
from dbos import DBOS, DBOSConfig
from typing import Optional, Callable, Union, Awaitable

def workflow(*args, **kwargs):
    return DBOS.workflow(*args, **kwargs)

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
    return DBOS.step(
        name=name,
        retries_allowed=retries_allowed,
        interval_seconds=interval_seconds,
        max_attempts=max_attempts,
        backoff_rate=backoff_rate,
        should_retry=should_retry,
    )

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
    resolved_db = db_url or os.environ.get("CHECKPOINT_DB_URL")
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
