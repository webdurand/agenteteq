import asyncio
import time
from src.agent.assistant import get_assistant

async def main():
    agent = get_assistant(session_id="test", extra_tools=[], channel="web")
    start = time.time()
    response_stream = await asyncio.to_thread(lambda: agent.run("Conta uma piada.", stream=True))
    
    first = False
    
    for chunk in response_stream:
        if not first:
            print(f"Time to first token: {time.time() - start:.2f}s")
            first = True
        print(chunk.content, end="", flush=True)
    print(f"\nTotal time: {time.time() - start:.2f}s")

asyncio.run(main())
