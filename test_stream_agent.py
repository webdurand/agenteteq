import asyncio
from src.agent.assistant import get_assistant

async def main():
    agent = get_assistant(session_id="test", extra_tools=[], channel="web")
    response_stream = agent.run("Conta uma piada.", stream=True)
    for chunk in response_stream:
        print(chunk.content, end="", flush=True)
    print()

asyncio.run(main())
