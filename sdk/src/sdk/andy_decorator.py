from dbos_openai_agents import DBOSRunner


async def agentic_runner(*args, **kwargs):
    return await DBOSRunner.run(*args, **kwargs)
