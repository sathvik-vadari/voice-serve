"""Main application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.helpers.config import Config
from app.helpers.logger import setup_logger
from app.db.connection import init_db
from app.routes import vapi_webhook_routes
from app.routes import ticket_routes
from app.routes import logistics_routes
from app.services.wakeup_scheduler import start_wakeup_scheduler, stop_wakeup_scheduler

logger = setup_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_wakeup_scheduler()
    yield
    stop_wakeup_scheduler()


app = FastAPI(
    title="Voice Commerce Service",
    description="Multi-LLM voice service – order anything, schedule wake-up calls, and more",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vapi_webhook_routes.router)
app.include_router(ticket_routes.router)
app.include_router(logistics_routes.router)


@app.get("/")
async def root():
    return {
        "service": "Voice Commerce Service",
        "status": "running",
        "version": "0.2.0",
        "endpoints": {
            "create_ticket": "POST /api/ticket",
            "get_ticket": "GET /api/ticket/{ticket_id}",
            "get_options": "GET /api/ticket/{ticket_id}/options",
            "confirm_order": "POST /api/ticket/{ticket_id}/confirm",
            "delivery_status": "GET /api/ticket/{ticket_id}/delivery",
            "vapi_webhook": "POST /api/vapi/webhook",
            "store_webhook": "POST /api/vapi/store-webhook",
            "logistics_callback": "POST /api/logistics/callback",
        },
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


def main():
    logger.info("Starting Voice Commerce Service")
    uvicorn.run(
        "app.main:app",
        host=Config.SERVER_HOST,
        port=Config.SERVER_PORT,
        reload=True,
        log_level=Config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
