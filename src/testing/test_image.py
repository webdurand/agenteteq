"""
Script de teste rápido para a API do Gemini Image Generation.
Roda diretamente sem depender do FastAPI/agente.

Uso: PYTHONPATH=. .venv/bin/python src/testing/test_image.py
"""
import os
import time
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def test_gemini_image():
    from src.tools.image_generation.nano_banana import NanoBananaProvider

    print("=" * 60)
    print("TESTE DE GERAÇÃO DE IMAGEM - NANO BANANA PRO")
    print("=" * 60)
    print(f"API Key: {os.getenv('GEMINI_API_KEY', 'NÃO CONFIGURADA')[:20]}...")
    print()

    provider = NanoBananaProvider()
    prompt = "A dramatic cinematic photo of a modern tech office at night, neon lights, ultrawide monitors, dark atmosphere"

    print(f"Prompt: {prompt}")
    print("Aguardando resposta da API...")
    print()

    start = time.time()
    try:
        image_bytes = await provider.generate(prompt)
        elapsed = time.time() - start
        print(f"✅ SUCESSO! Imagem gerada em {elapsed:.1f}s — {len(image_bytes)} bytes")

        # Salva o arquivo para inspecionar
        output_path = "/tmp/test_slide.jpg"
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        print(f"✅ Imagem salva em: {output_path}")

    except Exception as e:
        elapsed = time.time() - start
        import traceback
        print(f"❌ ERRO após {elapsed:.1f}s: {e}")
        print()
        print(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(test_gemini_image())
