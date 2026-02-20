"""Configuration helper for loading environment variables."""
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv('.env', override=True)


class Config:
    """Application configuration."""

    # Server Configuration
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")

    # Database (PostgreSQL) for wake-up call preferences and schedules
    DATABASE_URL: Optional[str] = os.getenv(
        "DATABASE_URL",
        "postgresql://localhost/phonos_wakeup"
    )

    # VAPI (voice AI phone calls)
    VAPI_API_KEY: Optional[str] = os.getenv("VAPI_API_KEY")
    VAPI_PHONE_NUMBER_ID: Optional[str] = os.getenv("VAPI_PHONE_NUMBER_ID")
    # Public URL where VAPI can reach this app (for webhooks). e.g. https://your-domain.com or ngrok URL for local
    VAPI_SERVER_URL: Optional[str] = os.getenv("VAPI_SERVER_URL")
    # Model: provider + model name. Credentials for the provider are set in VAPI dashboard (Integrations), not here.
    VAPI_MODEL_PROVIDER: str = os.getenv("VAPI_MODEL_PROVIDER", "openai")
    VAPI_MODEL: str = os.getenv("VAPI_MODEL", "gpt-4o")
    # Voice: if VAPI_VOICE_PROVIDER is set, voice is sent as { provider, voiceId }. Use openai + alloy for free test.
    VAPI_VOICE_PROVIDER: Optional[str] = os.getenv("VAPI_VOICE_PROVIDER")
    VAPI_VOICE: str = os.getenv("VAPI_VOICE", "alloy")

    @classmethod
    def validate(cls) -> None:
        """No required config at startup; missing values are handled at call time."""
        return None
