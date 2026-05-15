import asyncio
import os
import sys

from agents import Agent, Runner, function_tool
from ddgs import DDGS

from sdk import init, step, workflow


@function_tool
@step()
def search_web(query: str) -> str:
    """Search the web for information about a topic. Returns titles, URLs, and summaries."""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    if not results:
        return "No results found."
    formatted = []
    for r in results:
        formatted.append(f"Title: {r['title']}\nURL: {r['href']}\nSummary: {r['body']}")
    return "\n---\n".join(formatted)


agent = Agent(
    name="research-assistant",
    instructions="""You are a research assistant. Given a topic:
1. Search for information using search_web
2. Evaluate whether you have enough to write a thorough summary
3. If not, search again with a more specific or different query
4. Search at least twice before concluding
5. Synthesize findings into a clear, well-structured summary
Be explicit about what you found and what remains uncertain.""",
    tools=[search_web],
)


@workflow()
async def run_agent(topic: str) -> str:
    result = await Runner.run(starting_agent=agent, input=f"Research this topic thoroughly: {topic}")
    return str(result.final_output)


async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python agent.py <research topic>")
        sys.exit(1)

    topic = " ".join(sys.argv[1:])
    print(f"\nResearching: {topic}\n")
    output = await run_agent(topic)
    print("\n=== RESEARCH SUMMARY ===")
    print(output)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    init(
        name="research-assistant",
        db_url=os.environ.get("DB_URL"),
    )
    asyncio.run(main())
