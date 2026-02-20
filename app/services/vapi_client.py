"""VAPI API client for outbound phone calls."""
import logging
from typing import Any, Optional

import aiohttp

from app.helpers.config import Config
from app.schemas.vapi_tools import get_vapi_wakeup_tools

logger = logging.getLogger(__name__)

VAPI_CALL_PHONE_URL = "https://api.vapi.ai/call/phone"


def _get_wakeup_assistant(server_url: str, system_prompt: str, *, include_server_url: bool = True) -> dict[str, Any]:
    """Build inline assistant config for wake-up calls (used for outbound and assistant-request)."""
    base = (server_url or "").rstrip("/")
    webhook_url = f"{base}/api/vapi/webhook" if base else ""
    voice_config: Any = Config.VAPI_VOICE
    if Config.VAPI_VOICE_PROVIDER:
        voice_config = {"provider": Config.VAPI_VOICE_PROVIDER, "voiceId": Config.VAPI_VOICE}
    assistant = {
        "firstMessage": "Hi, this is your wake-up call. How can I help you today?",
        "model": {
            "provider": Config.VAPI_MODEL_PROVIDER,
            "model": Config.VAPI_MODEL,
            "messages": [{"role": "system", "content": system_prompt}],
            "functions": [t["function"] for t in get_vapi_wakeup_tools()],
        },
        "voice": voice_config,
    }
    if include_server_url and webhook_url:
        assistant["serverUrl"] = webhook_url
    elif include_server_url and not base:
        logger.warning(
            "VAPI_SERVER_URL is not set: outbound calls will not have tool-calling. "
            "Set VAPI_SERVER_URL to a public URL (e.g. ngrok) so VAPI can reach your webhook."
        )
    return assistant


async def create_phone_call(
    customer_number: str,
    system_prompt: str,
    *,
    assistant_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Create an outbound phone call via VAPI.
    Uses VAPI_PHONE_NUMBER_ID and VAPI_SERVER_URL from config.
    """
    api_key = Config.VAPI_API_KEY
    phone_number_id = Config.VAPI_PHONE_NUMBER_ID
    server_url = Config.VAPI_SERVER_URL

    if not api_key:
        logger.error("VAPI_API_KEY not set")
        return {"success": False, "error": "VAPI_API_KEY not configured"}
    if not phone_number_id:
        logger.error("VAPI_PHONE_NUMBER_ID not set")
        return {"success": False, "error": "VAPI_PHONE_NUMBER_ID not configured"}

    assistant = _get_wakeup_assistant(server_url or "", system_prompt, include_server_url=bool(server_url))
    if assistant_overrides:
        for k, v in assistant_overrides.items():
            if v is not None:
                assistant[k] = v

    payload = {
        "assistant": assistant,
        "phoneNumberId": phone_number_id,
        "customer": {"number": customer_number},
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(VAPI_CALL_PHONE_URL, headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    logger.info("VAPI call created: customer=%s", customer_number)
                    return {"success": True, "call": data}
                text = await resp.text()
                logger.error("VAPI call failed: %s %s", resp.status, text)
                return {"success": False, "error": f"VAPI returned {resp.status}", "body": text}
    except Exception as e:
        logger.exception("VAPI create_phone_call failed")
        return {"success": False, "error": str(e)}


def get_wakeup_assistant_for_webhook(server_url: str, system_prompt: str) -> dict[str, Any]:
    """Same as _get_wakeup_assistant, for use in webhook assistant-request response."""
    return _get_wakeup_assistant(server_url, system_prompt)
