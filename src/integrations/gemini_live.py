import os
import json
import base64
import asyncio
import websockets
import logging

logger = logging.getLogger(__name__)

class GeminiLiveClient:
    def __init__(self, model: str = None, voice_name: str = None, system_instruction: str = None, tools: list = None):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY nao configurada")
            
        self.model = model or os.getenv("VOICE_REALTIME_MODEL", "models/gemini-2.5-flash-native-audio-preview-12-2025")
        if not self.model.startswith("models/"):
            self.model = f"models/{self.model}"
            
        self.voice_name = voice_name or os.getenv("VOICE_REALTIME_VOICE", "Puck")
        self.system_instruction = system_instruction or "Você é o Teq, um assistente prestativo."
        self.tools = tools or []
        
        self.ws = None
        self.url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={self.api_key}"
        
    async def connect(self):
        self.ws = await websockets.connect(self.url)
        await self._send_setup()
        
    async def _send_setup(self):
        setup_msg = {
            "setup": {
                "model": self.model,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": self.voice_name
                            }
                        }
                    },
                    "thinkingConfig": {
                        "thinkingBudget": 0
                    }
                },
                "realtimeInputConfig": {
                    "automaticActivityDetection": {
                        "disabled": False,
                        "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
                        "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                        "prefixPaddingMs": 200,
                        "silenceDurationMs": 500
                    }
                },
                "systemInstruction": {
                    "parts": [{"text": self.system_instruction}]
                }
            }
        }
        
        if self.tools:
            setup_msg["setup"]["tools"] = [{"functionDeclarations": self.tools}]
            
        await self.ws.send(json.dumps(setup_msg))
        
        while True:
            response_raw = await self.ws.recv()
            if isinstance(response_raw, bytes):
                try:
                    response_raw = response_raw.decode("utf-8")
                except UnicodeDecodeError:
                    logger.info("[Gemini Live] Recebeu bytes nao-decodificaveis durante setup (%s bytes)", len(response_raw))
                    continue

            response = json.loads(response_raw)
            if "setupComplete" in response:
                logger.info("[Gemini Live] Setup completo")
                break
            else:
                logger.info("[Gemini Live] Esperando setup, recebeu: %s", list(response.keys()))
                
    async def send_audio_chunk(self, pcm_bytes: bytes):
        if not self.ws:
            return
            
        # O modelo espera PCM 16-bit, 16kHz, mono encodado em base64
        msg = {
            "realtimeInput": {
                "mediaChunks": [{
                    "mimeType": "audio/pcm;rate=16000",
                    "data": base64.b64encode(pcm_bytes).decode('utf-8')
                }]
            }
        }
        await self.ws.send(json.dumps(msg))
        
    async def send_tool_response(self, call_id: str, function_name: str, response_dict: dict):
        if not self.ws:
            return
            
        msg = {
            "toolResponse": {
                "functionResponses": [{
                    "id": call_id,
                    "name": function_name,
                    "response": response_dict
                }]
            }
        }
        await self.ws.send(json.dumps(msg))

    async def cancel_response(self):
        if not self.ws:
            return

        # Best-effort cancel: sinaliza fim de turno do cliente para interromper resposta em curso.
        msg = {
            "clientContent": {
                "turns": [],
                "turnComplete": True
            }
        }
        await self.ws.send(json.dumps(msg))
        
    async def receive_loop(self, on_audio, on_tool_call, on_turn_complete, on_interrupted=None):
        audio_chunk_count = 0
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    try:
                        message = message.decode("utf-8")
                    except UnicodeDecodeError:
                        continue

                if isinstance(message, str):
                    data = json.loads(message)
                    
                    if "serverContent" in data:
                        server_content = data["serverContent"]
                        
                        if server_content.get("interrupted"):
                            logger.info("[Gemini Live] interrupted (after %s audio chunks)", audio_chunk_count)
                            audio_chunk_count = 0
                            if on_interrupted:
                                await on_interrupted()
                        
                        model_turn = server_content.get("modelTurn")
                        if model_turn:
                            parts = model_turn.get("parts", [])
                            for part in parts:
                                if "inlineData" in part:
                                    mime = part["inlineData"].get("mimeType", "")
                                    b64_data = part["inlineData"].get("data", "")
                                    if mime.startswith("audio/pcm") and b64_data:
                                        pcm_out = base64.b64decode(b64_data)
                                        await on_audio(pcm_out)
                                        audio_chunk_count += 1
                                        
                                if "functionCall" in part:
                                    fc = part["functionCall"]
                                    call_id = fc.get("id")
                                    name = fc.get("name")
                                    args = fc.get("args", {})
                                    logger.info("[Gemini Live] functionCall (part): %s args=%s", name, args)
                                    await on_tool_call(call_id, name, args)

                                if "text" in part:
                                    logger.info("[Gemini Live] text: %s", part['text'][:120])
                                    
                        if server_content.get("turnComplete"):
                            logger.info("[Gemini Live] turnComplete (sent %s audio chunks)", audio_chunk_count)
                            audio_chunk_count = 0
                            await on_turn_complete()
                            
                    elif "toolCall" in data:
                        tool_call_data = data.get("toolCall") or {}
                        function_calls = tool_call_data.get("functionCalls", [])
                        for fc in function_calls:
                            call_id = fc.get("id")
                            name = fc.get("name")
                            args = fc.get("args", {})
                            logger.info("[Gemini Live] toolCall (root): %s args=%s", name, args)
                            await on_tool_call(call_id, name, args)
                    elif "setupComplete" not in data:
                        logger.info("[Gemini Live] msg: %s", list(data.keys()))
        except websockets.exceptions.ConnectionClosed:
            logger.info("[Gemini Live] Conexão encerrada")
        except Exception as e:
            logger.error("[Gemini Live] Erro no loop de recebimento: %s", e)
            
    async def close(self):
        if self.ws:
            await self.ws.close()
            self.ws = None
