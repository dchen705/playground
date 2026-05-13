import os
from pathlib import Path

from agents import Agent, function_tool
from dbos import DBOS, DBOSConfig, SetWorkflowID
from dbos._error import DBOSNonExistentWorkflowError
from dbos_openai_agents import DBOSRunner
from dotenv import load_dotenv
from fastapi import FastAPI, Path as FastAPIPath, Query
from pydantic import BaseModel, Field
from sdk import workflow, step, agentic_runner

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

app = FastAPI()
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
SAMPLE_MESSAGE = (
    "Plan a day in San Francisco based on weather, forecast, air quality, "
    "and activity options. Show the evidence you used."
)
CONDUCTOR_NOTE = (
    "DBOS/Conductor contains model-call checkpoints and any tool steps the "
    "agent chose to run."
)
CRASH_MARKER_DIR = Path("/tmp/dbos-agentic-loop-crashes")
CRASH_REQUEST_DIR = Path("/tmp/dbos-agentic-loop-crash-requests")


class AgentRequest(BaseModel):
    message: str = Field(default=SAMPLE_MESSAGE, examples=[SAMPLE_MESSAGE])


class AgentResponse(BaseModel):
    workflow_id: str
    output: str
    note: str


@function_tool
# @DBOS.step()
@step()
async def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    DBOS.logger.info("Tool input: get_weather(city=%s)", city)
    result = f"{city}: sunny, 68F, light west wind, comfortable humidity."
    DBOS.logger.info("Tool output: get_weather -> %s", result)
    return result


def crash_once_during_forecast(workflow_id: str) -> None:
    request_path = CRASH_REQUEST_DIR / workflow_id.replace("/", "_")
    if not request_path.exists():
        return

    marker_path = CRASH_MARKER_DIR / workflow_id.replace("/", "_")
    if marker_path.exists():
        DBOS.logger.info("Crash marker already exists; continuing forecast")
        return

    CRASH_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("crashed once during forecast\n", encoding="utf-8")
    DBOS.logger.info("Intentional demo crash during get_forecast")
    os._exit(42)


@function_tool
# @DBOS.step()
@step()
async def get_forecast(city: str, days: int, current_weather: str) -> str:
    """Get a forecast using the exact current-weather evidence from get_weather."""
    DBOS.logger.info(
        "Tool input: get_forecast(city=%s, days=%s, current_weather=%s)",
        city,
        days,
        current_weather,
    )
    if current_weather.strip().upper() == "PENDING" or not current_weather.startswith(
        f"{city}:"
    ):
        result = (
            "DEPENDENCY_MISSING: call get_weather first, then pass its exact "
            "output as current_weather."
        )
        DBOS.logger.info("Tool output: get_forecast -> %s", result)
        return result

    result = (
        f"{city}: next {days} days stay mild. Morning fog clears by 10 AM, "
        "afternoons are mostly sunny, evening temperatures fall into the low 60s. "
        f"Current-weather basis: {current_weather}"
    )
    DBOS.logger.info("Tool output: get_forecast -> %s", result)
    crash_once_during_forecast(DBOS.workflow_id)
    return result


@function_tool
# @DBOS.step()
@step()
async def get_air_quality(city: str, forecast_summary: str) -> str:
    """Get air quality using the exact forecast evidence from get_forecast."""
    DBOS.logger.info(
        "Tool input: get_air_quality(city=%s, forecast_summary=%s)",
        city,
        forecast_summary,
    )
    if (
        forecast_summary.strip().upper() == "PENDING"
        or "Current-weather basis:" not in forecast_summary
    ):
        result = (
            "DEPENDENCY_MISSING: call get_forecast first, then pass its exact "
            "output as forecast_summary."
        )
        DBOS.logger.info("Tool output: get_air_quality -> %s", result)
        return result

    result = (
        f"{city}: AQI 42, good air quality, low pollen, no smoke advisory. "
        f"Forecast basis: {forecast_summary}"
    )
    DBOS.logger.info("Tool output: get_air_quality -> %s", result)
    return result


@function_tool
# @DBOS.step()
@step()
async def get_activity_recommendations(city: str, planning_context: str) -> str:
    """Get activity recommendations using the exact combined planning context."""
    DBOS.logger.info(
        "Tool input: get_activity_recommendations(city=%s, planning_context=%s)",
        city,
        planning_context,
    )
    if (
        planning_context.strip().upper() == "PENDING"
        or "AQI" not in planning_context
        or "forecast" not in planning_context.lower()
    ):
        result = (
            "DEPENDENCY_MISSING: call get_air_quality first, then pass a combined "
            "planning_context containing the weather, forecast, and air-quality "
            "outputs."
        )
        DBOS.logger.info("Tool output: get_activity_recommendations -> %s", result)
        return result

    result = (
        f"{city}: good fits include a late-morning waterfront walk, outdoor "
        "lunch, an afternoon museum stop if wind picks up, and sunset at Twin Peaks. "
        f"Planning basis: {planning_context}"
    )
    DBOS.logger.info("Tool output: get_activity_recommendations -> %s", result)
    return result


agent = Agent(
    name="planning-demo-agent",
    model=MODEL,
    tools=[
        get_weather,
        get_forecast,
        get_air_quality,
        get_activity_recommendations,
    ],
    instructions=(
        "You are a careful planning agent. Decide what information you need, "
        "use the available tools when they are helpful, and then give a concise "
        "answer. Do not reveal hidden chain-of-thought; summarize only the "
        "evidence you used and any practical caveats. Some tools depend on the "
        "exact output of earlier tools; do not invent dependency arguments."
    ),
)


def expand_prompt(message: str) -> str:
    return (
        f"{message}\n\n"
        "Decide for yourself which available tools, if any, are useful before "
        "answering. You may call one tool, several tools, or no tools if the "
        "request is already answerable. If you do use tools, base the answer on "
        "the evidence they return.\n\n"
        "Tool dependency guidance:\n"
        "- Call get_weather first if current conditions are needed.\n"
        "- Call get_forecast only after get_weather, passing the exact weather "
        "result as current_weather.\n"
        "- Call get_air_quality only after get_forecast, passing the exact "
        "forecast result as forecast_summary.\n"
        "- Call get_activity_recommendations only after collecting enough "
        "planning context, passing the exact combined context you want it to use.\n\n"
        "Never use placeholders such as PENDING or UNKNOWN for dependency "
        "arguments. If a tool returns DEPENDENCY_MISSING, call the missing "
        "prerequisite tool and then retry with the exact returned evidence.\n\n"
        "Final answer format:\n"
        "- Evidence used\n"
        "- Recommendation\n"
        "- Caveats or backup option"
    )


# @DBOS.workflow()
@workflow()
async def run_agent(message: str) -> dict[str, str]:
    # result = await DBOSRunner.run(agent, expand_prompt(message))
    result = await agentic_runner(agent, expand_prompt(message))

    return {
        "workflow_id": DBOS.workflow_id,
        "output": str(result.final_output),
        "note": CONDUCTOR_NOTE,
    }


@app.on_event("startup")
async def startup() -> None:
    config: DBOSConfig = {
        "name": os.environ.get("DBOS_APP_NAME", "agent-demo"),
        "system_database_url": os.environ["DBOS_SYSTEM_DATABASE_URL"],
        "conductor_key": os.environ["DBOS_CONDUCTOR_KEY"],
    }

    DBOS(config=config)
    DBOS.launch()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/{workflow_id}", response_model=AgentResponse)
async def agent_endpoint(
    request: AgentRequest,
    workflow_id: str = FastAPIPath(
        default=...,
        examples=["dependent-demo-1"],
        description="Use a new ID for each fresh demo, such as dependent-demo-2.",
    ),
    crash_during_forecast: bool = Query(
        default=False,
        description="Set true to intentionally crash once during get_forecast.",
    ),
) -> AgentResponse:
    return await run_agent_workflow(
        workflow_id,
        request.message,
        crash_during_forecast,
    )


@app.post("/demo/agent/{workflow_id}", response_model=AgentResponse)
async def demo_agent_endpoint(
    workflow_id: str = FastAPIPath(
        default=...,
        examples=["dependent-demo-1"],
        description="Use a new ID for each fresh demo, such as dependent-demo-2.",
    ),
    crash_during_forecast: bool = Query(
        default=False,
        description="Set true to intentionally crash once during get_forecast.",
    ),
) -> AgentResponse:
    return await run_agent_workflow(
        workflow_id,
        SAMPLE_MESSAGE,
        crash_during_forecast,
    )


async def run_agent_workflow(
    workflow_id: str,
    message: str,
    crash_during_forecast: bool,
) -> AgentResponse:
    try:
        handle = await DBOS.retrieve_workflow_async(workflow_id)
    except DBOSNonExistentWorkflowError:
        if crash_during_forecast:
            CRASH_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
            request_path = CRASH_REQUEST_DIR / workflow_id.replace("/", "_")
            request_path.write_text("crash during forecast\n", encoding="utf-8")

        with SetWorkflowID(workflow_id):
            handle = await DBOS.start_workflow_async(run_agent, message)

    result = await handle.get_result()
    return AgentResponse(**result)
