import os
import asyncio
import time
from src.integrations.tts import get_tts

async def main():
    tts = get_tts()
    start = time.time()
    await tts.synthesize("Beleza!")
    print(f"Time for 'Beleza!': {time.time() - start:.2f}s")
    
    start = time.time()
    await tts.synthesize("Ah, de novo? Tá bom, lá vai outra.")
    print(f"Time for sentence: {time.time() - start:.2f}s")

asyncio.run(main())
