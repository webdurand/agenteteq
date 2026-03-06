import os
import asyncio
import time
from google import genai
from google.genai import types

async def main():
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    text = "Olá, eu sou a sua assistente. Estou testando a latência de streaming. Espero que a primeira parte do áudio chegue muito rápido para que possamos tocar em tempo real."
    
    start = time.time()
    def _call():
        return client.models.generate_content_stream(
            model="gemini-2.5-flash-preview-tts",
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="Aoede"
                        )
                    )
                ),
            ),
        )
    
    response_stream = await asyncio.to_thread(_call)
    first_chunk = False
    
    # We must iterate over the stream
    def _consume():
        nonlocal first_chunk
        for chunk in response_stream:
            if not first_chunk:
                latency = time.time() - start
                print(f"Time to first audio chunk: {latency:.2f}s")
                first_chunk = True
            
            if chunk.candidates and chunk.candidates[0].content.parts:
                part = chunk.candidates[0].content.parts[0]
                if hasattr(part, 'inline_data') and part.inline_data:
                    data = part.inline_data.data
                    print(f"Received audio chunk of {len(data)} bytes")

    await asyncio.to_thread(_consume)
    print(f"Total time: {time.time() - start:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())
