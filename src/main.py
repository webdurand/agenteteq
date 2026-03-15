import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv
import sentry_sdk

load_dotenv()

_sentry_dsn = os.getenv("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from src.endpoints.whatsapp import router as whatsapp_router
from src.endpoints.web import router as web_router
from src.endpoints.voice_live import router as voice_live_router
from src.auth.routes import router as auth_router
from src.endpoints.api import router as api_router
from src.endpoints.admin import router as admin_router
from src.endpoints.admin_billing import router as admin_billing_router
from src.endpoints.billing import router as billing_router, webhook_router
from src.endpoints.carousel import router as carousel_router
from src.endpoints.integrations import router as integrations_router
from src.endpoints.social import router as social_router
from src.endpoints.branding import router as branding_router
from src.scheduler.engine import start_scheduler, shutdown_scheduler
from src.events import set_main_loop
from src.db.init import ensure_tables
from src.models.subscriptions import ensure_default_plans
from src.queue.task_queue import recover_stale_tasks
from src.events_broadcast import listen_ws_events


async def _deferred_startup():
    """Executa tarefas pesadas de startup em background, sem bloquear health checks."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, ensure_tables)
    await loop.run_in_executor(None, ensure_default_plans)
    await loop.run_in_executor(None, recover_stale_tasks)
    await loop.run_in_executor(None, start_scheduler)
    logger.info("Startup em background finalizado.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    asyncio.create_task(listen_ws_events())
    asyncio.create_task(_deferred_startup())
    yield
    shutdown_scheduler()


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Agente WhatsApp - Diario Teq", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
_allowed_origins = [
    _frontend_origin,
    "https://teq.ia.br",
    "https://agenteteq-front.vercel.app",
    "http://localhost:5173",
    "http://localhost:3000",
]
# Deduplica mantendo ordem
_allowed_origins = list(dict.fromkeys(_allowed_origins))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor"})

@app.get("/health")
async def health():
    return {"status": "ok"}

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
app.include_router(integrations_router)
app.include_router(social_router)
app.include_router(branding_router)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "API do Agente WhatsApp esta rodando!"}
