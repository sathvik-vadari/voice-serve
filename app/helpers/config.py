"""Configuration helper for loading environment variables."""
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv('.env', override=True)


class Config:
    """Application configuration."""

    # Server
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")

    # Database (PostgreSQL)
    DATABASE_URL: Optional[str] = os.getenv(
        "DATABASE_URL",
        "postgresql://localhost/phonos_wakeup"
    )

    # OpenAI (used by orchestrator, product research, transcript analyzer)
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    # Google Maps Places API
    GOOGLE_MAPS_API_KEY: Optional[str] = os.getenv("GOOGLE_MAPS_API_KEY")

    # Google Gemini (query intelligence, store enrichment)
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # VAPI (voice AI phone calls)
    VAPI_API_KEY: Optional[str] = os.getenv("VAPI_API_KEY")
    VAPI_PHONE_NUMBER_ID: Optional[str] = os.getenv("VAPI_PHONE_NUMBER_ID")
    VAPI_SERVER_URL: Optional[str] = os.getenv("VAPI_SERVER_URL")
    VAPI_MODEL_PROVIDER: str = os.getenv("VAPI_MODEL_PROVIDER", "openai")
    VAPI_MODEL: str = os.getenv("VAPI_MODEL", "gpt-4o")
    VAPI_VOICE_PROVIDER: Optional[str] = os.getenv("VAPI_VOICE_PROVIDER")
    VAPI_VOICE: str = os.getenv("VAPI_VOICE", "alloy")
    VAPI_VOICE_MODEL: Optional[str] = os.getenv("VAPI_VOICE_MODEL")
    VAPI_VOICE_LANGUAGE: str = os.getenv("VAPI_VOICE_LANGUAGE", "hi")

    # Limits
    MAX_STORES_TO_CALL: int = int(os.getenv("MAX_STORES_TO_CALL", "5"))
    MAX_ALTERNATIVES: int = int(os.getenv("MAX_ALTERNATIVES", "3"))

    # Test mode
    TEST_MODE: bool = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")
    TEST_PHONE: str = os.getenv("TEST_PHONE", "")

    @classmethod
    def validate(cls) -> None:
        """No required config at startup; missing values are handled at call time."""
        return None
