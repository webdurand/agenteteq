import os
import asyncio
import time
from google import genai
from google.genai import types

async def synthesize_sentence(client, text: str):
    start = time.time()
    response = await asyncio.to_thread(
        lambda: client.models.generate_content(
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
    )
    latency = time.time() - start
    print(f"TTS latency for '{text}': {latency:.2f}s")
    return response

async def main():
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    sentences = [
        "Olá, eu sou a sua assistente.",
        "Como posso ajudar você hoje?",
        "Estou testando a latência do sistema."
    ]
    for s in sentences:
        await synthesize_sentence(client, s)

asyncio.run(main())
