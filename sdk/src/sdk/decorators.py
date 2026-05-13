import os
from dbos import DBOS, DBOSConfig


def workflow(*args, **kwargs):
    return DBOS.workflow(*args, **kwargs)


def step(*args, **kwargs):
    return DBOS.step(*args, **kwargs)


sleep = DBOS.sleep


def init(
    name: str,
    db_url: str | None = None,
    traces_endpoint: str | None = None,
    env: str | None = None,
) -> None:
    resolved_env = env or os.environ.get("CHECKPOINT_ENV", "development")
    resolved_db = db_url or os.environ.get("CHECKPOINT_DB_URL")

    config: DBOSConfig = {
        "name": name,
        # Falls back to SQLite ([name].sqlite) if not set — good for local dev
        "system_database_url": resolved_db,
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
