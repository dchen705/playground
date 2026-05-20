import asyncio
import functools
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from dbos import DBOS, DBOSConfig
from dbos_openai_agents import DBOSRunner


def workflow(
    *,
    name: str | None = None,
    max_recovery_attempts: int | None = 5,
):
    return DBOS.workflow(name=name, max_recovery_attempts=max_recovery_attempts)


def step(
    *,
    name: str | None = None,
    retries_allowed: bool = False,
    interval_seconds: float = 1.0,
    max_attempts: int = 3,
    backoff_rate: float = 2.0,
    should_retry: Callable[[BaseException], bool | Awaitable[bool]] | None = None,
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
        step_name = fn.__name__

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapped_step(*args: Any, **kwargs: Any):
                started_at = _log_step_started(step_name)
                try:
                    result = await fn(*args, **kwargs)
                    _log_step_succeeded(step_name, started_at)
                    return result
                except Exception as exc:
                    _log_step_failed(step_name, started_at, exc)
                    raise

        else:

            @functools.wraps(fn)
            def wrapped_step(*args: Any, **kwargs: Any):
                started_at = _log_step_started(step_name)
                try:
                    result = fn(*args, **kwargs)
                    _log_step_succeeded(step_name, started_at)
                    return result
                except Exception as exc:
                    _log_step_failed(step_name, started_at, exc)
                    raise

        return dbos_step(wrapped_step)

    return decorator


async def sleep(*args, **kwargs):
    return await DBOS.sleep_async(*args, **kwargs)


async def agentic_runner(*args, **kwargs):
    return await DBOSRunner.run(*args, **kwargs)


logger = DBOS.logger


def init(
    name: str,
    db_url: str | None = None,
    conductor_key: str | None = None,
) -> None:
    resolved_db = (
        db_url
        or os.environ.get("DB_URL")
        or os.environ.get("DBOS_SYSTEM_DATABASE_URL")
        or os.environ.get("CHECKPOINT_DB_URL")
    )
    resolved_conductor_key = conductor_key or os.environ.get("CHECKPOINT_CONDUCTOR_KEY")

    config: DBOSConfig = {
        "name": name,
        "system_database_url": resolved_db,
    }
    if resolved_conductor_key is not None:
        config["conductor_key"] = resolved_conductor_key

    DBOS(config=config)
    DBOS.launch()

    if resolved_db and resolved_db.startswith("postgresql"):
        from sdk.tracing import register_checkpoint_tracing_processor

        register_checkpoint_tracing_processor(resolved_db)


def _log_step_started(step_name: str) -> float:
    logger.info("step %s started", step_name)
    return time.monotonic()


def _log_step_succeeded(step_name: str, started_at: float) -> None:
    logger.info("step %s done (%.2fs)", step_name, time.monotonic() - started_at)


def _log_step_failed(step_name: str, started_at: float, exc: Exception) -> None:
    logger.error(
        "step %s failed (%.2fs): %s", step_name, time.monotonic() - started_at, exc
    )
