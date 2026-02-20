"""Main application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.helpers.config import Config
from app.helpers.logger import setup_logger
from app.routes import vapi_webhook_routes
from app.services.wakeup_scheduler import start_wakeup_scheduler, stop_wakeup_scheduler

logger = setup_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start wake-up scheduler on startup; stop on shutdown."""
    start_wakeup_scheduler()
    yield
    stop_wakeup_scheduler()


app = FastAPI(
    title="VAPI Wake-Up Bot",
    description="Wake-up call bot using VAPI phone calls",
    version="0.1.0",
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


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "VAPI Wake-Up Bot",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


def main():
    """Main function to run the server."""
    logger.info("Starting VAPI Wake-Up Bot")
    uvicorn.run(
        "app.main:app",
        host=Config.SERVER_HOST,
        port=Config.SERVER_PORT,
        reload=True,
        log_level=Config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
