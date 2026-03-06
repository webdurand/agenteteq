import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from src.endpoints.whatsapp import router as whatsapp_router
from src.endpoints.web import router as web_router
from src.endpoints.voice_live import router as voice_live_router
from src.auth.routes import router as auth_router
from src.endpoints.api import router as api_router
from src.endpoints.admin import router as admin_router
from src.endpoints.admin_billing import router as admin_billing_router
from src.endpoints.billing import router as billing_router, webhook_router
from src.endpoints.carousel import router as carousel_router
from src.scheduler.engine import start_scheduler, shutdown_scheduler
from src.events import set_main_loop
from src.db.init import ensure_tables
from src.queue.task_queue import recover_stale_tasks
from src.events_broadcast import listen_ws_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    ensure_tables()
    recover_stale_tasks()
    asyncio.create_task(listen_ws_events())
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Agente WhatsApp - Diario Teq", lifespan=lifespan)

_frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_origin, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor"})

app.include_router(whatsapp_router)
app.include_router(web_router)
app.include_router(voice_live_router)
app.include_router(auth_router)
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(admin_billing_router)
app.include_router(billing_router)
app.include_router(webhook_router)
app.include_router(carousel_router)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "API do Agente WhatsApp esta rodando!"}
