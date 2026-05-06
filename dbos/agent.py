"""
Agent loop using DBOS durable execution primitives.

Compare this to ../agent.py — the logic is the same, but each LLM call
and tool execution is decorated with @DBOS.step() so DBOS can checkpoint,
retry, and visualize each step.

DBOS differs from Inngest in a few key ways:
- Durability is backed by PostgreSQL (not a separate dev server)
- Steps are decorated functions, not wrapped lambdas
- Workflows are decorated with @DBOS.workflow()
- Has its own cloud dashboard at https://console.dbos.dev
"""

import json
from dbos import DBOS
from openai import OpenAI
from tools.base import tool_to_openai_schema, execute_tool

client = OpenAI()
DBOS()


def load_workflow(name: str) -> dict:
    import importlib
    module = importlib.import_module(f"workflows.{name}")
    return module.config


# --- Durable steps ---
# Each of these is checkpointed by DBOS. If the process crashes,
# DBOS replays the workflow and skips already-completed steps.

@DBOS.step()
def call_llm(messages: list, tools: list) -> dict:
    """Make an LLM call. Checkpointed by DBOS."""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools if tools else None,
    )
    choice = response.choices[0]
    message = choice.message

    tool_calls = []
    if message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]

    return {
        "message": {
            "role": "assistant",
            "content": message.content,
            "tool_calls": tool_calls if tool_calls else None,
        },
        "tool_calls": tool_calls,
    }


@DBOS.step()
def exec_tool(tool_call_data: dict, tools: list) -> str:
    """Execute a tool call. Checkpointed by DBOS."""

    class FakeFunctionCall:
        def __init__(self, data):
            self.function = type("F", (), {
                "name": data["function"]["name"],
                "arguments": data["function"]["arguments"],
            })()

    return execute_tool(FakeFunctionCall(tool_call_data), tools)


# --- The workflow ---

@DBOS.workflow()
def run_agent(workflow_name: str, user_message: str) -> dict:
    """
    The agent loop. Decorated with @DBOS.workflow() so the entire
    run is tracked and can be recovered if the process crashes.
    """
    config = load_workflow(workflow_name)

    messages = [{"role": "system", "content": config["system_prompt"]}]
    messages.append({"role": "user", "content": user_message})
    openai_tools = [tool_to_openai_schema(t) for t in config["tools"]]

    for turn in range(config["max_turns"]):

        # --- LLM call (durable step) ---
        llm_response = call_llm(messages, openai_tools)
        messages.append(llm_response["message"])

        # --- Check if done ---
        if not llm_response["tool_calls"]:
            return {
                "status": "complete",
                "turns": turn + 1,
                "answer": llm_response["message"]["content"],
                "workflow": workflow_name,
            }

        # --- Execute each tool call (each is a durable step) ---
        for tool_call_data in llm_response["tool_calls"]:
            tool_result = exec_tool(tool_call_data, config["tools"])

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_data["id"],
                "content": tool_result,
            })

    return {
        "status": "max_turns_reached",
        "turns": config["max_turns"],
        "workflow": workflow_name,
    }
