import os
from pathlib import Path

from agents import Agent
from dbos_openai_agents import DBOSRunner
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from sdk import workflow, step, init

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

app = FastAPI()

agent = Agent(
    name="demo-agent",
    instructions="You are a helpful assistant. Keep responses short and clear.",
)


class AgentRequest(BaseModel):
    message: str


class AgentResponse(BaseModel):
    output: str


@step()
def example_step(message: str) -> str:
    print(message)


@workflow()
async def run_agent(message: str) -> str:
    example_step("step one")
    result = await DBOSRunner.run(agent, message)
    return str(result.final_output)


@app.on_event("startup")
async def startup() -> None:
    init(
        name=os.environ.get("DBOS_APP_NAME", "agent-demo"),
        db_url=os.environ.get("DBOS_SYSTEM_DATABASE_URL"),
        conductor_key=os.environ.get("DBOS_CONDUCTOR_KEY"),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent", response_model=AgentResponse)
async def agent_endpoint(request: AgentRequest) -> AgentResponse:
    output = await run_agent(request.message)
    return AgentResponse(output=output)
