import os
import asyncio
from google import genai
from google.genai import types

async def test_gemini_tts():
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    text = "Olá Durand, tudo bem? Aqui é a sua nova voz."
    try:
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
                )
            )
        )
        print("Success with just text! Model: gemini-2.5-flash-preview-tts")
    except Exception as e:
        print(f"Failed: {e}")

asyncio.run(test_gemini_tts())
