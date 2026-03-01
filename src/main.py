import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from src.endpoints.whatsapp import router as whatsapp_router
from src.scheduler.engine import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Agente WhatsApp - Diario Teq", lifespan=lifespan)

app.include_router(whatsapp_router)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API do Agente WhatsApp esta rodando!"}
