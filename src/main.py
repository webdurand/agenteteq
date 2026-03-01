import os
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from src.endpoints.whatsapp import router as whatsapp_router

app = FastAPI(title="Agente WhatsApp - Diario Teq")

app.include_router(whatsapp_router)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API do Agente WhatsApp está rodando!"}
