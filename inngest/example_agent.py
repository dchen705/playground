"""
Agent loop using Inngest durable execution primitives.

Compare this to ../agent.py — the logic is the same, but each LLM call
and tool execution is wrapped in step.run() so Inngest can checkpoint,
retry, and visualize each step in its dashboard.

This file is the core of what you're evaluating. The questions to answer:
- How does the dashboard show each step?
- What happens when a step fails / retries?
- How readable is the run history?
- How does it handle the agent loop pattern (variable number of steps)?
"""

import json
import inngest
from openai import OpenAI
from tools.base import tool_to_openai_schema, execute_tool


client = OpenAI()
inngest_client = inngest.Inngest(app_id="playground-inngest")


def load_workflow(name: str) -> dict:
    import importlib
    module = importlib.import_module(f"workflows.{name}")
    return module.config


@inngest_client.create_function(
    fn_id="agent-run",
    trigger=inngest.TriggerEvent(event="agent/run"),
    # Optional: set a longer timeout since agent runs can take a while
    # cancel=inngest.Cancel(event="agent/cancel"),
)
async def run_agent(ctx: inngest.Context, step: inngest.Step):
    """
    The agent loop. Each LLM call and tool execution is a separate
    durable step, so Inngest can checkpoint and retry each one.
    """
    workflow_name = ctx.event.data.get("workflow", "deep_research")
    user_message = ctx.event.data["input"]
    config = load_workflow(workflow_name)

    messages = [{"role": "system", "content": config["system_prompt"]}]
    messages.append({"role": "user", "content": user_message})
    openai_tools = [tool_to_openai_schema(t) for t in config["tools"]]

    for turn in range(config["max_turns"]):

        # --- LLM call (durable step) ---
        # If this fails, Inngest retries it without re-running previous steps.
        llm_response = await step.run(
            f"llm-call-turn-{turn}",
            lambda: _call_llm(messages, openai_tools),
        )

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
        for i, tool_call_data in enumerate(llm_response["tool_calls"]):
            tool_name = tool_call_data["function"]["name"]

            tool_result = await step.run(
                f"tool-{turn}-{tool_name}-{i}",
                lambda tc=tool_call_data, cfg=config: _exec_tool(tc, cfg["tools"]),
            )

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


def _call_llm(messages: list, tools: list) -> dict:
    """
    Make the LLM call and return a serializable dict.
    (step.run requires the return value to be JSON-serializable.)
    """
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools if tools else None,
    )
    choice = response.choices[0]
    message = choice.message

    # Serialize the message so Inngest can store it
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


def _exec_tool(tool_call_data: dict, tools) -> str:
    """Execute a tool call. Wrapped so step.run can checkpoint it."""

    class FakeFunctionCall:
        """Minimal shim to match what execute_tool expects."""
        def __init__(self, data):
            self.function = type("F", (), {
                "name": data["function"]["name"],
                "arguments": data["function"]["arguments"],
            })()

    return execute_tool(FakeFunctionCall(tool_call_data), tools)
